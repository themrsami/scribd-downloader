import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  buildLines,
  buildParagraphs,
  convertLineImagesToText,
  cssColorToHex,
  findRepeatingFooters,
  layoutPage,
  paragraphText,
  removeCoveredRuns,
  renderText,
  startsWithListMarker,
} from '../lib/layout.js';
import { run } from './helpers.mjs';

const lineTexts = (lines) => lines.map((line) => line.segments.map((segment) => segment.text).join(''));
const paragraphTexts = (lines, width = 900) => buildParagraphs(lines, width).map(paragraphText);

test('runs on one line are joined left to right with sensible spaces', () => {
  assert.deepEqual(lineTexts(buildLines([run('world', 70, 100.4), run('Hello ', 10, 100)])), ['Hello world']);
  const runs = [
    run('It', 10, 100, { w: 12 }),
    run('’s', 23, 100, { w: 10 }), // touching: no space
    run('fine', 60, 100, { w: 30 }), // gap: space
    run('.', 95, 100, { w: 4 }), // gap but punctuation: no space
  ];
  assert.deepEqual(lineTexts(buildLines(runs)), ['It’s fine.']);
  assert.deepEqual(lineTexts(buildLines([run(' Total: ', 10, 100), run('42 ', 90, 100)])), ['Total: 42']);
  assert.deepEqual(buildLines([run(' ', 10, 100), run('   ', 10, 200)]), []);
});

test('bullets drawn in tiny or private-use fonts stay on their line', () => {
  const tiny = [
    run('First item', 118, 591, { size: 16.6 }),
    run('•', 88.8, 594.1, { size: 6.65, w: 4 }),
    run('Second line', 113, 612.8, { size: 16.6 }),
  ];
  assert.deepEqual(lineTexts(buildLines(tiny)), ['• First item', 'Second line']);

  const privateUse = [run('', 170, 104, { size: 6, w: 4 }), run('Look at the structure', 199, 100, { w: 300 })];
  assert.deepEqual(lineTexts(buildLines(privateUse)), ['• Look at the structure']);
});

test('a wide gap becomes a tab stop', () => {
  const [line] = buildLines([run('HARVARD UNIVERSITY', 60, 100, { w: 600 }), run('Cambridge, MA', 820, 100)]);
  assert.deepEqual(lineTexts([line]), ['HARVARD UNIVERSITY\tCambridge, MA']);
  assert.deepEqual(line.tab_stops, [820]);
});

test('wrapped lines merge into paragraphs; gaps, short lines and lists split them', () => {
  const wrapped = buildLines([
    run('This is a long sentence that wraps onto', 50, 100, { w: 800 }),
    run('the next line of the page.', 50, 120, { w: 300 }),
    run('New paragraph after a gap.', 50, 170, { w: 300 }),
  ]);
  assert.deepEqual(paragraphTexts(wrapped), [
    'This is a long sentence that wraps onto the next line of the page.',
    'New paragraph after a gap.',
  ]);

  const hyphen = buildLines([run('A well-known user-', 50, 100, { w: 800 }), run('friendly tool.', 50, 120, { w: 200 })]);
  assert.deepEqual(paragraphTexts(hyphen), ['A well-known user-friendly tool.']);

  const list = buildLines([
    run('1. Professional Design: a clean layout that', 50, 100, { w: 800 }),
    run('looks polished.', 70, 120, { w: 200 }),
    run('2. Strategic Sections: contact, summary.', 50, 140, { w: 800 }),
    run('• bullet item', 50, 160, { w: 800 }),
  ]);
  assert.deepEqual(paragraphTexts(list), [
    '1. Professional Design: a clean layout that looks polished.',
    '2. Strategic Sections: contact, summary.',
    '• bullet item',
  ]);

  const sizes = buildLines([run('Big Heading', 50, 100, { size: 32, w: 800 }), run('Body text', 50, 140, { w: 800 })]);
  assert.equal(buildParagraphs(sizes, 900).length, 2);
});

test('centred headings are detected; body lines and list items are not', () => {
  const [title] = buildParagraphs(buildLines([run('Title', 400, 100, { w: 100 })]), 900);
  assert.equal(title.centered, true);
  const [left] = buildParagraphs(buildLines([run('Left', 50, 100, { w: 100 })]), 900);
  assert.equal(left.centered, false);
  const [body] = buildParagraphs(buildLines([run('A body line spanning most of the page width', 150, 100, { w: 720 })]), 1000);
  assert.equal(body.centered, false);
});

test('styles are kept per segment', () => {
  const [line] = buildLines([
    run('Name:', 50, 100, { w: 60, bold: true, color: 'rgb(255, 0, 0)', font: 'Times New Roman' }),
    run(' Jane', 112, 100, { w: 50 }),
  ]);
  assert.deepEqual(line.segments[0].style, ['Times New Roman', 16, 'FF0000', true, false, 0]);
  assert.deepEqual(line.segments[1].style, ['Arial', 16, '000000', false, false, 0]);
  assert.equal(cssColorToHex('rgba(0, 128, 255, 0.5)'), '0080FF');
  assert.equal(cssColorToHex('#abc'), 'AABBCC');
  assert.equal(cssColorToHex('transparent'), null);
});

test('list markers', () => {
  for (const text of ['• item', '- item', '1. item', '12) item', 'a) item', '(iv) item']) {
    assert.equal(startsWithListMarker(text), true, text);
  }
  for (const text of ['Item', '1.5 million', '-5 degrees']) assert.equal(startsWithListMarker(text), false, text);
});

test('text hidden under an image patch is dropped (edited PDFs)', () => {
  const layered = (text, x, y, z, w) => ({ ...run(text, x, y, { w }), z });
  const runs = [
    layered('City, State', 868, 548, 2, 73), // old text, under the patch
    layered('City, State (or Remote)', 775, 546, 4, 165), // new text, on top
    layered('ORGANIZATION', 60, 548, 2, 146),
  ];
  const patch = { src: 'https://cdn.test/images/1-abc.jpg', x: 696, y: 545, w: 245, h: 22, z: 3 };
  assert.deepEqual(removeCoveredRuns(runs, [patch], 1000, 1300).map((r) => r.text), [
    'City, State (or Remote)',
    'ORGANIZATION',
  ]);

  // Text inside a transparent PNG box is kept.
  const box = { ...patch, src: 'https://cdn.test/images/2-abc.png' };
  const inside = [layered('Callout text', 700, 548, 2, 100)];
  assert.equal(removeCoveredRuns(inside, [box], 1000, 1300).length, 1);

  // OCR text under a full-page scan is the only text there is.
  const scan = { src: 'https://cdn.test/1-scan.jpg', x: 0, y: 0, w: 1000, h: 1300, z: 3 };
  assert.equal(removeCoveredRuns([layered('Scanned words', 100, 100, 2)], [scan], 1000, 1300).length, 1);
});

test('fill-in blanks drawn as thin images become underscores', () => {
  const runs = [run('Study abroad coursework in ', 60, 418, { size: 16.6, w: 200 }), run('. ', 311, 418, { size: 16.6, w: 8 })];
  const blank = { src: 'blank.jpg', x: 260, y: 432, w: 50, h: 1.1 };
  const divider = { src: 'rule.jpg', x: 58, y: 202, w: 882, h: 2.2 };
  const [newRuns, remaining] = convertLineImagesToText(runs, [blank, divider], 1000);
  assert.deepEqual(remaining, [divider]);
  assert.deepEqual(lineTexts(buildLines(newRuns)), ['Study abroad coursework in ______.']);
});

test('page layout: image bullets, floating images, reading order', () => {
  const page = {
    pageNum: 1,
    width: 1000,
    height: 1300,
    runs: [
      run('Concise: short and clear.', 170, 322, { size: 18, w: 400 }),
      run('Readable: easy to scan.', 170, 394, { size: 18, w: 400 }),
      run('Text inside a shaded box.', 200, 610, { w: 400 }),
    ],
    images: [
      { src: 'dot.jpg', x: 155, y: 328, w: 7.8, h: 7.8 },
      { src: 'dot.jpg', x: 155, y: 400, w: 7.8, h: 7.8 },
      { src: 'box.png', x: 150, y: 584, w: 700, h: 88 },
      { src: 'rule.png', x: 80, y: 800, w: 800, h: 2 },
      { src: 'figure.png', x: 60, y: 900, w: 400, h: 200 },
    ],
  };
  const blocks = layoutPage(page);
  assert.deepEqual(
    blocks.map((block) => (block.type === 'paragraph' ? paragraphText(block) : `${block.src}:${block.behind}`)),
    [
      '• Concise: short and clear.',
      '• Readable: easy to scan.',
      'box.png:true',
      'Text inside a shaded box.',
      'rule.png:true',
      'figure.png:false',
    ]
  );
});

test('plain text has one marker per page', () => {
  const pages = [
    { pageNum: 1, width: 900, runs: [run('Page one text.', 50, 50, { w: 800 })], images: [] },
    { pageNum: 2, width: 900, runs: [], images: [] },
  ];
  assert.equal(renderText(pages), '===== Page 1 =====\n\nPage one text.\n\n===== Page 2 =====\n');
});

test('repeating bottom lines are footers; one-off text is not', () => {
  const page = (number) => ({
    pageNum: number,
    width: 1000,
    height: 1300,
    runs: [
      run(`Body text on page ${number}.`, 80, 100, { w: 300 }),
      run('Course Manual', 80, 1200, { w: 150 }),
      run(`Confidential 2018 Workday, Inc. ${number}`, 80, 1230, { w: 400 }),
    ],
    images: [],
  });
  const pages = [1, 2, 3, 4, 5].map(page);
  pages[2].runs.push(run('A closing sentence that appears once.', 80, 1160, { w: 500 }));
  const footers = findRepeatingFooters(pages, pages.map(layoutPage));
  assert.deepEqual([...footers].sort(), ['confidential # workday, inc. #', 'course manual']);
  assert.equal(findRepeatingFooters(pages.slice(0, 2), pages.slice(0, 2).map(layoutPage)).size, 0);
});
