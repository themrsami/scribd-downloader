import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  baseNameFromUrl,
  normalizeFormats,
  parseScribdUrl,
  planDownloads,
  readLinksFromText,
  redactUrl,
  sanitizeFilename,
} from '../lib/url.js';
import { classifyLoadFailure } from '../lib/diagnostics.js';

const EMBED = 'https://www.scribd.com/embeds/123456789/content';

test('accepts Scribd document links in every common shape', () => {
  const links = [
    'https://www.scribd.com/document/123456789/Document-Title',
    'https://www.scribd.com/doc/123456789/Document-Title',
    'https://de.scribd.com/document/123456789/Document-Title',
    'https://es.scribd.com/document/123456789/Document-Title',
    'https://scribd.com/document/123456789/Document-Title',
    'http://www.scribd.com/document/123456789/Document-Title',
    'www.scribd.com/document/123456789/Document-Title',
    'scribd.com/document/123456789',
    'https://www.scribd.com/document/123456789/',
    'https://www.scribd.com/document/123456789/Title?utm_source=x#page=2',
    'https://WWW.SCRIBD.COM/document/123456789/Title',
    'https://www.scribd.com/embeds/123456789/content',
    '  https://de.scribd.com/document/123456789/Title  ',
  ];
  for (const link of links) {
    const info = parseScribdUrl(link);
    assert.ok(info, link);
    assert.equal(info.docId, '123456789', link);
    assert.equal(info.embedUrl, EMBED, link);
  }
});

test('rejects lookalike domains and non-document pages', () => {
  const links = [
    '',
    'not a url',
    'https://notscribd.com/document/123456789/Title',
    'https://evil-scribd.com/document/123456789/Title',
    'https://scribd.com.evil.com/document/123456789/Title',
    'https://www.scribd.com/user/123456789/someone',
    'https://www.scribd.com/document/abc/Title',
    'ftp://www.scribd.com/document/123456789/Title',
    'https://www.scribd.com/',
  ];
  for (const link of links) assert.equal(parseScribdUrl(link), null, link);
});

test('keeps and encodes secret_password, and hides it when shown', () => {
  const info = parseScribdUrl('https://de.scribd.com/document/123456789/Title?x=1&secret_password=a%2Bb%26c%3D');
  assert.equal(info.secretPassword, 'a+b&c=');
  assert.equal(info.embedUrl, `${EMBED}?secret_password=a%2Bb%26c%3D`);
  assert.equal(redactUrl(`${EMBED}?secret_password=AbC123&x=1`), `${EMBED}?secret_password=REDACTED&x=1`);
  assert.equal(redactUrl(EMBED), EMBED);
});

test('file names are safe on every system', () => {
  assert.equal(baseNameFromUrl('https://www.scribd.com/document/123456789/Document-Title'), 'Document-Title');
  assert.equal(baseNameFromUrl('https://de.scribd.com/document/123456789/Mein%20Dokument'), 'Mein Dokument');
  assert.equal(baseNameFromUrl('https://www.scribd.com/document/123456789'), '123456789');
  assert.equal(baseNameFromUrl('https://www.scribd.com/document/123456789/A%3AB%3FC'), 'A_B_C');
  assert.equal(sanitizeFilename('a<b>c:d"e/f\\g|h?i*j'), 'a_b_c_d_e_f_g_h_i_j');
  assert.equal(sanitizeFilename('  .hidden.  '), 'hidden');
  assert.equal(sanitizeFilename(''), 'scribd_document');
});

test('pasted text is split into links; comments and blank lines are ignored', () => {
  const text = '# my list\n\nhttps://a\r\n  https://b   https://c\n# done\n';
  assert.deepEqual(readLinksFromText(text), ['https://a', 'https://b', 'https://c']);
  assert.deepEqual(readLinksFromText(''), []);
});

test('bulk plan drops duplicates, reports bad links, avoids name clashes', () => {
  const a = 'https://www.scribd.com/document/111/Report';
  const b = 'https://de.scribd.com/document/222/Report';
  const c = 'https://www.scribd.com/document/333/Other';
  const plan = planDownloads([a, b, a, 'https://example.com/x', c]);
  assert.deepEqual(plan.invalid, ['https://example.com/x']);
  assert.equal(plan.duplicates.length, 1);
  assert.deepEqual(plan.jobs.map((job) => job.info.docId), ['111', '222', '333']);
  assert.deepEqual(plan.jobs.map((job) => job.baseName), ['Report', 'Report-222', 'Other']);

  // Documents already downloading are skipped; their names stay reserved.
  const second = planDownloads([a, c], { ids: new Set(['111']), names: new Set(['other']) });
  assert.equal(second.duplicates.length, 1);
  assert.deepEqual(second.jobs.map((job) => job.baseName), ['Other-333']);
});

test('formats are normalised and default to PDF', () => {
  assert.deepEqual(normalizeFormats(['DOCX', 'pdf', 'docx', 'epub']), ['pdf', 'docx']);
  assert.deepEqual(normalizeFormats([]), ['pdf']);
  assert.deepEqual(normalizeFormats(undefined), ['pdf']);
});

test('explains why a document did not load', () => {
  const cases = [
    ['Just a moment...', '', '', 'bot-check'],
    ['', 'Verify you are human by completing the action below.', '', 'bot-check'],
    ['Access denied', '', '', 'denied'],
    // Exactly what scribd.com shows for a deleted / missing document.
    ['Scribd', "Document Not Found\nSorry, we can't display this document.", '', 'not-found'],
    ['Scribd', '', 'https://www.scribd.com/embeds/1/content?secret_password=x', 'bad-password'],
    ['Scribd', 'This document is private', '', 'private'],
    ['Log in', '', 'https://www.scribd.com/login', 'login'],
    ['Scribd', '', 'https://www.scribd.com/embeds/1/content', 'no-pages'],
  ];
  for (const [title, body, url, code] of cases) {
    const reason = classifyLoadFailure(title, body, url);
    assert.equal(reason.code, code, `${title} / ${body}`);
    assert.ok(reason.message.length > 20);
  }
});
