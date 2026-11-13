// Adds a small "Download" button to Scribd document pages.
// It hands the page's link to the extension; the download itself runs in the
// background, so this script stays tiny. Styles live in a shadow root so the
// page and the button cannot affect each other.

(() => {
  if (window.__scribdDownloaderButton) return;
  window.__scribdDownloaderButton = true;

  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:20px;bottom:20px;z-index:2147483647;';
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `
    <style>
      :host { all: initial; }
      .wrap { display: flex; align-items: center; gap: 6px; font: 14px/1.2 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
      button { font: inherit; cursor: pointer; border: 0; }
      /* Cream, black and grey, to match the popup. */
      .wrap { gap: 0; box-shadow: 0 6px 18px rgba(0, 0, 0, 0.28); border-radius: 3px; }
      .main {
        display: flex; align-items: center; gap: 8px;
        max-width: 320px; padding: 10px 14px; border-radius: 3px 0 0 3px;
        border: 1px solid #141414; background: #141414; color: #fbf8f1; font-weight: 600;
      }
      .main:hover { background: #2c2c2b; }
      .main:focus-visible, .close:focus-visible { outline: 2px solid #141414; outline-offset: 2px; }
      .main.done, .main.error { background: #fbf8f1; color: #141414; }
      .main svg { flex: none; width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: square; }
      .label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .close {
        align-self: stretch; display: grid; place-items: center; width: 30px; border-radius: 0 3px 3px 0;
        border: 1px solid #141414; border-left: 0; background: #f3eee3; color: #4a4a48; font-size: 16px; line-height: 1;
      }
      .close:hover { background: #e2dccf; color: #141414; }
    </style>
    <div class="wrap">
      <button class="main" type="button" title="Save this document with Scribd Downloader">
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 3v9m0 0-3.5-3.5M10 12l3.5-3.5M4 15.5h12"/></svg>
        <span class="label" role="status">Download</span>
      </button>
      <button class="close" type="button" aria-label="Hide download button" title="Hide">&times;</button>
    </div>
  `;

  const main = root.querySelector('.main');
  const label = root.querySelector('.label');
  let jobId = null;
  let busy = false;

  function show(text, state = '') {
    label.textContent = text;
    main.className = `main ${state}`.trim();
    main.title = text;
  }

  async function start() {
    if (busy) return;
    busy = true;
    show('Starting…');
    try {
      const result = await chrome.runtime.sendMessage({ type: 'enqueue', urls: [location.href], fromPage: true });
      if (result?.error) throw new Error(result.error);
      if (result.added) show('Waiting…');
      else if (result.duplicates) show('Already downloading…');
      else {
        busy = false;
        show('Not a document page', 'error');
      }
    } catch (error) {
      busy = false;
      // Happens when the extension was reloaded or updated.
      show('Reload the page and try again', 'error');
    }
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== 'job-update') return;
    const job = message.job;
    if (jobId && job.id !== jobId && job.status !== 'running') return;
    jobId = job.id;
    if (job.status === 'running') {
      const percent = job.total ? ` ${Math.floor((100 * job.page) / job.total)}%` : '';
      show(job.step === 'Downloading pages' ? `Downloading${percent}` : `${job.step || 'Starting'}…`);
    } else if (job.status === 'done') {
      busy = false;
      jobId = null;
      show('✓ Saved to Downloads', 'done');
    } else if (job.status === 'cancelled') {
      busy = false;
      jobId = null;
      show('Download');
    } else if (job.status === 'error') {
      busy = false;
      jobId = null;
      show(`Failed: ${job.error || 'try again'}`, 'error');
    }
  });

  main.addEventListener('click', start);
  root.querySelector('.close').addEventListener('click', () => host.remove());
  document.documentElement.append(host);
})();
