// Service workers cannot create blob: URLs, and chrome.downloads needs one
// to save a file. This hidden page turns stored files into blob: URLs.

import { deleteBlob, getBlob } from './lib/store.js';

const urls = new Map();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.target !== 'offscreen') return false;

  if (message.type === 'create-url') {
    getBlob(message.key)
      .then((blob) => {
        if (!blob) throw new Error('File data is missing');
        const url = URL.createObjectURL(blob);
        urls.set(message.key, url);
        sendResponse({ url });
      })
      .catch((error) => sendResponse({ error: String(error?.message || error) }));
    return true;
  }

  if (message.type === 'release-url') {
    const url = urls.get(message.key);
    if (url) URL.revokeObjectURL(url);
    urls.delete(message.key);
    deleteBlob(message.key)
      .catch(() => {})
      .then(() => sendResponse({ ok: true }));
    return true;
  }

  return false;
});
