import { normalizeFormats, parseScribdUrl, readLinksFromText } from '../lib/url.js';

const FORMAT_NAMES = { pdf: 'PDF', docx: 'Word', txt: 'Text' };
const ICONS = {
  cancel: 'M5 5l10 10M15 5L5 15',
  retry: 'M4.5 10a5.5 5.5 0 1 0 1.7-4M4 3.5v3h3',
  folder: 'M3 6.5a1.5 1.5 0 0 1 1.5-1.5h3l1.5 1.8h6.5A1.5 1.5 0 0 1 17 8.3v6.2a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z',
  remove: 'M5 5l10 10M15 5L5 15',
};

const $ = (id) => document.getElementById(id);
const els = {
  current: $('current'),
  currentTitle: $('current-title'),
  linksCard: $('links-card'),
  links: $('links'),
  toggleLinks: $('toggle-links'),
  download: $('download'),
  downloadLabel: $('download-label'),
  feedback: $('feedback'),
  queue: $('queue'),
  jobs: $('jobs'),
  clear: $('clear'),
  formats: [...document.querySelectorAll('input[name="format"]')],
};

/** The Scribd document open in the current tab, if any. */
let currentDoc = null;
/** true = download the current page; false = use the links box. */
let usePage = false;

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function selectedFormats() {
  return els.formats.filter((input) => input.checked).map((input) => input.value);
}

function setFeedback(text, bad = false) {
  els.feedback.textContent = text;
  els.feedback.classList.toggle('bad', bad);
}

function refreshButton() {
  const formats = selectedFormats();
  const links = usePage ? 1 : readLinksFromText(els.links.value).length;
  els.download.disabled = !formats.length || !links;
  els.downloadLabel.textContent = links > 1 ? `Download ${links} documents` : 'Download';
}

function setMode(page) {
  usePage = page && Boolean(currentDoc);
  els.current.hidden = !usePage;
  els.linksCard.hidden = usePage;
  els.toggleLinks.hidden = !currentDoc;
  els.toggleLinks.textContent = usePage ? 'Download other links instead' : 'Download this page instead';
  if (!usePage) els.links.focus();
  refreshButton();
}

function titleFromTab(tab, info) {
  const cleaned = (tab.title || '').replace(/\s*[|–—-]\s*Scribd\s*$/i, '').replace(/\s*\|\s*PDF.*$/i, '').trim();
  if (cleaned && !/^scribd$/i.test(cleaned)) return cleaned;
  return info.slug ? info.slug.replace(/[-_]+/g, ' ') : `Document ${info.docId}`;
}

// ---------------------------------------------------------------- jobs list

function iconButton(kind, label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'icon-button';
  button.title = label;
  button.setAttribute('aria-label', label);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 20 20');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[kind]);
  svg.append(path);
  button.append(svg);
  button.addEventListener('click', onClick);
  return button;
}

function statusText(job) {
  // Same order as the "Save as" choices.
  const formats = Object.keys(FORMAT_NAMES)
    .filter((format) => job.formats.includes(format))
    .map((format) => FORMAT_NAMES[format])
    .join(', ');
  switch (job.status) {
    case 'queued':
      return `Waiting · ${formats}`;
    case 'running':
      if (job.total && job.step === 'Downloading pages') return `Page ${job.page} of ${job.total}`;
      return `${job.step || 'Starting'}…`;
    case 'done': {
      const pages = `${job.total} page${job.total === 1 ? '' : 's'}`;
      return job.warning ? `Saved · ${pages} · ${job.warning}` : `Saved · ${pages} · ${formats}`;
    }
    case 'cancelled':
      return 'Cancelled';
    default:
      return job.error || 'Something went wrong.';
  }
}

function renderJob(job) {
  const item = document.createElement('li');
  item.className = `job ${job.status}`;

  const title = document.createElement('div');
  title.className = 'job-title';
  title.textContent = job.title || job.name;
  title.title = job.displayUrl || '';

  const status = document.createElement('div');
  status.className = 'job-status';
  status.textContent = statusText(job);

  const actions = document.createElement('div');
  actions.className = 'job-actions';
  if (job.status === 'queued' || job.status === 'running') {
    actions.append(iconButton('cancel', 'Cancel', () => send({ type: 'cancel', id: job.id })));
  } else {
    if (job.status === 'done' && job.files?.length) {
      actions.append(iconButton('folder', 'Show in folder', () => chrome.downloads.show(job.files[0].downloadId)));
    } else {
      actions.append(iconButton('retry', 'Try again', () => send({ type: 'retry', id: job.id })));
    }
    actions.append(iconButton('remove', 'Remove from list', () => send({ type: 'remove', id: job.id })));
  }

  item.append(title, actions, status);

  if (job.status === 'running') {
    const bar = document.createElement('div');
    bar.className = 'bar';
    const fill = document.createElement('i');
    const known = job.total > 0 && job.step === 'Downloading pages';
    if (known) fill.style.width = `${Math.max(3, (100 * job.page) / job.total)}%`;
    else bar.classList.add('busy');
    bar.setAttribute('role', 'progressbar');
    if (known) {
      bar.setAttribute('aria-valuemin', '0');
      bar.setAttribute('aria-valuemax', String(job.total));
      bar.setAttribute('aria-valuenow', String(job.page));
    }
    bar.append(fill);
    item.append(bar);
  }
  return item;
}

function renderJobs(jobs) {
  const list = [...(jobs || [])].reverse(); // newest first
  els.queue.hidden = !list.length;
  els.clear.hidden = !list.some((job) => !['queued', 'running'].includes(job.status));
  els.jobs.replaceChildren(...list.map(renderJob));
}

// ---------------------------------------------------------------- actions

async function startDownload() {
  const formats = normalizeFormats(selectedFormats());
  const urls = usePage ? [currentDoc.url] : readLinksFromText(els.links.value);
  if (!urls.length) return;

  els.download.disabled = true;
  try {
    const result = await send({ type: 'enqueue', urls, formats });
    if (result?.error) throw new Error(result.error);

    const notes = [];
    if (result.added) notes.push(result.added === 1 ? 'Download started.' : `${result.added} downloads added.`);
    if (result.duplicates) notes.push(`${result.duplicates} already in the list.`);
    if (result.invalid.length) {
      notes.push(
        result.invalid.length === 1
          ? 'One link is not a Scribd document link.'
          : `${result.invalid.length} links are not Scribd document links.`
      );
    }
    setFeedback(notes.join(' '), !result.added);
    if (!usePage) els.links.value = result.invalid.join('\n');
  } catch (error) {
    setFeedback(`Could not start: ${error.message}`, true);
  }
  refreshButton();
}

async function init() {
  const [{ formats }, session, tabs] = await Promise.all([
    chrome.storage.local.get('formats'),
    chrome.storage.session.get('jobs'),
    chrome.tabs.query({ active: true, currentWindow: true }),
  ]);

  const saved = normalizeFormats(formats);
  for (const input of els.formats) input.checked = saved.includes(input.value);

  // popup.html?url=...&title=... previews the popup for a given page (used
  // when the popup is opened as a normal tab, e.g. by the UI tests).
  const preview = new URLSearchParams(location.search);
  const tab = preview.get('url') ? { url: preview.get('url'), title: preview.get('title') || '' } : tabs[0];
  const info = tab?.url ? parseScribdUrl(tab.url) : null;
  if (info) {
    currentDoc = { url: tab.url, info };
    els.currentTitle.textContent = titleFromTab(tab, info);
  }
  setMode(Boolean(currentDoc));
  renderJobs(session.jobs);

  for (const input of els.formats) {
    input.addEventListener('change', () => {
      chrome.storage.local.set({ formats: selectedFormats() });
      refreshButton();
    });
  }
  els.links.addEventListener('input', () => {
    setFeedback('');
    refreshButton();
  });
  els.links.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) startDownload();
  });
  els.toggleLinks.addEventListener('click', () => setMode(!usePage));
  els.download.addEventListener('click', startDownload);
  els.clear.addEventListener('click', () => send({ type: 'clear-finished' }));

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'session' && changes.jobs) renderJobs(changes.jobs.newValue);
  });
}

init();
