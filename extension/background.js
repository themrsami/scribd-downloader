// Service worker: owns the download queue.
//
// The popup and the in-page button only send messages ("enqueue", "cancel",
// ...). Job state lives in chrome.storage.session, so any open popup updates
// itself by listening for storage changes.

import { buildDocx } from './lib/docx.js';
import { CancelledError, exportDocument } from './lib/engine.js';
import { createImageLoader } from './lib/images.js';
import { renderText } from './lib/layout.js';
import { clearBlobs, putBlob } from './lib/store.js';
import { normalizeFormats, planDownloads, redactUrl } from './lib/url.js';

const MIME = {
  pdf: 'application/pdf',
  txt: 'text/plain;charset=utf-8',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
};
const ACTIVE = new Set(['queued', 'running']);

/** @type {Array<object>} */
let jobs = [];
let running = false;
let loaded = null;
const cancelled = new Set();

// ---------------------------------------------------------------- state

function load() {
  if (!loaded) {
    loaded = (async () => {
      const stored = await chrome.storage.session.get('jobs');
      jobs = stored.jobs || [];
      // A job that was running when the worker stopped cannot be resumed.
      for (const job of jobs) {
        if (job.status === 'running') {
          job.status = 'error';
          job.error = 'The download was interrupted. Try again.';
        }
      }
      await clearBlobs().catch(() => {});
      await save();
    })();
  }
  return loaded;
}

async function save() {
  await chrome.storage.session.set({ jobs });
  updateBadge();
}

function updateBadge() {
  const active = jobs.filter((job) => ACTIVE.has(job.status));
  const current = jobs.find((job) => job.status === 'running');
  let text = '';
  if (current && current.total) text = `${Math.floor((100 * current.page) / current.total)}%`;
  else if (active.length) text = String(active.length);
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color: '#141414' });
  chrome.action.setBadgeTextColor?.({ color: '#FBF8F1' });
}

function notifyTab(job) {
  if (job.sourceTabId === undefined || job.sourceTabId === null) return;
  const { id, status, page, total, error, step } = job;
  chrome.tabs.sendMessage(job.sourceTabId, { type: 'job-update', job: { id, status, page, total, error, step } }).catch(() => {});
}

let lastSave = 0;
async function update(job, changes, { force = false } = {}) {
  Object.assign(job, changes);
  // Page-by-page progress is frequent; write at most ~5 times a second.
  if (force || Date.now() - lastSave > 200) {
    lastSave = Date.now();
    await save();
    notifyTab(job);
  }
}

// ---------------------------------------------------------------- saving

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['BLOBS'],
    justification: 'Create a blob URL so the finished document can be saved to Downloads.',
  });
}

function waitForDownload(downloadId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => finish(resolve), 120_000);
    const finish = (settle, value) => {
      clearTimeout(timeout);
      chrome.downloads.onChanged.removeListener(listener);
      settle(value);
    };
    const listener = (delta) => {
      if (delta.id !== downloadId || !delta.state) return;
      if (delta.state.current === 'complete') finish(resolve);
      if (delta.state.current === 'interrupted') {
        finish(reject, new Error(`Chrome could not save the file (${delta.error?.current || 'interrupted'}).`));
      }
    };
    chrome.downloads.onChanged.addListener(listener);
  });
}

/** Save bytes to the Downloads folder. Returns Chrome's download id. */
async function saveFile(bytes, filename, mime) {
  const key = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  await putBlob(key, new Blob([bytes], { type: mime }));
  await ensureOffscreen();
  try {
    const created = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'create-url', key });
    if (!created?.url) throw new Error(created?.error || 'Could not prepare the file.');
    const downloadId = await chrome.downloads.download({
      url: created.url,
      filename,
      conflictAction: 'uniquify',
      saveAs: false,
    });
    await waitForDownload(downloadId);
    return downloadId;
  } finally {
    await chrome.runtime.sendMessage({ target: 'offscreen', type: 'release-url', key }).catch(() => {});
  }
}

// ---------------------------------------------------------------- queue

async function runJob(job) {
  await update(job, { status: 'running', step: 'Opening document', page: 0, total: 0, error: '' }, { force: true });

  const result = await exportDocument(job, {
    isCancelled: () => cancelled.has(job.id),
    onProgress: (progress) => {
      update(job, progress);
    },
  });

  const files = [];
  if (result.pdf) {
    await update(job, { step: 'Saving PDF' }, { force: true });
    files.push({ format: 'pdf', downloadId: await saveFile(result.pdf, `${job.name}.pdf`, MIME.pdf) });
  }
  if (job.formats.includes('txt')) {
    await update(job, { step: 'Saving text' }, { force: true });
    const text = new TextEncoder().encode(renderText(result.pages));
    files.push({ format: 'txt', downloadId: await saveFile(text, `${job.name}.txt`, MIME.txt) });
  }
  let warning = '';
  if (job.formats.includes('docx')) {
    await update(job, { step: 'Building Word file' }, { force: true });
    const loadImage = createImageLoader();
    // Download the document's images several at a time before building.
    await loadImage.preload(result.pages.flatMap((page) => page.images.map((image) => image.src)));
    const { bytes, failedImages } = await buildDocx(result.pages, { loadImage });
    if (failedImages) warning = `${failedImages} image${failedImages === 1 ? '' : 's'} could not be added to the Word file.`;
    await update(job, { step: 'Saving Word file' }, { force: true });
    files.push({ format: 'docx', downloadId: await saveFile(bytes, `${job.name}.docx`, MIME.docx) });
  }

  await update(
    job,
    { status: 'done', step: '', page: result.pageCount, total: result.pageCount, files, warning },
    { force: true }
  );
}

async function pump() {
  if (running) return;
  running = true;
  try {
    for (;;) {
      const job = jobs.find((candidate) => candidate.status === 'queued');
      if (!job) break;
      try {
        await runJob(job);
      } catch (error) {
        const wasCancelled = error instanceof CancelledError || cancelled.has(job.id);
        await update(
          job,
          wasCancelled
            ? { status: 'cancelled', step: '', error: '' }
            : { status: 'error', step: '', error: String(error?.message || error) },
          { force: true }
        );
      }
      cancelled.delete(job.id);
    }
  } finally {
    running = false;
    if (await chrome.offscreen.hasDocument().catch(() => false)) {
      await chrome.offscreen.closeDocument().catch(() => {});
    }
  }
}

async function enqueue(urls, formats, sourceTabId) {
  const active = jobs.filter((job) => ACTIVE.has(job.status));
  const plan = planDownloads(urls, {
    ids: new Set(active.map((job) => job.docId)),
    names: new Set(active.map((job) => job.name)),
  });
  const chosen = normalizeFormats(formats);

  for (const item of plan.jobs) {
    jobs.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      docId: item.info.docId,
      name: item.baseName,
      title: item.info.slug ? item.info.slug.replace(/[-_]+/g, ' ').trim() : `Document ${item.info.docId}`,
      displayUrl: redactUrl(item.url.trim()),
      embedUrl: item.info.embedUrl,
      formats: chosen,
      status: 'queued',
      step: '',
      page: 0,
      total: 0,
      error: '',
      warning: '',
      files: [],
      sourceTabId: sourceTabId ?? null,
    });
  }
  // Keep the list short: drop the oldest finished jobs.
  while (jobs.length > 50) {
    const index = jobs.findIndex((job) => !ACTIVE.has(job.status));
    if (index < 0) break;
    jobs.splice(index, 1);
  }
  await save();
  pump();
  return { added: plan.jobs.length, invalid: plan.invalid.map(redactUrl), duplicates: plan.duplicates.length };
}

async function handle(message, sender) {
  await load();
  switch (message.type) {
    case 'enqueue': {
      let formats = message.formats;
      if (!formats) formats = (await chrome.storage.local.get('formats')).formats;
      return enqueue(message.urls || [], formats, message.fromPage ? sender.tab?.id : null);
    }
    case 'cancel': {
      const job = jobs.find((candidate) => candidate.id === message.id);
      if (job?.status === 'running') cancelled.add(job.id);
      else if (job?.status === 'queued') Object.assign(job, { status: 'cancelled' });
      await save();
      return { ok: true };
    }
    case 'retry': {
      const job = jobs.find((candidate) => candidate.id === message.id);
      if (job && !ACTIVE.has(job.status)) {
        Object.assign(job, { status: 'queued', error: '', warning: '', page: 0, total: 0, files: [] });
        await save();
        pump();
      }
      return { ok: true };
    }
    case 'remove':
      jobs = jobs.filter((job) => job.id !== message.id || ACTIVE.has(job.status));
      await save();
      return { ok: true };
    case 'clear-finished':
      jobs = jobs.filter((job) => ACTIVE.has(job.status));
      await save();
      return { ok: true };
    default:
      return undefined;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.target === 'offscreen') return false;
  handle(message, sender)
    .then((response) => sendResponse(response))
    .catch((error) => sendResponse({ error: String(error?.message || error) }));
  return true;
});

chrome.runtime.onStartup.addListener(() => load());
chrome.runtime.onInstalled.addListener(() => load().then(pump));
load().then(pump);
