import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildDocx, escapeXml } from '../lib/docx.js';
import { crc32, createZip } from '../lib/zip.js';
import { PNG, run, unzip, unzipText } from './helpers.mjs';

const loadImage = async (src, crop) => ({ data: PNG, crop });

function samplePages() {
  const body = (number, extra = []) => ({
    pageNum: number,
    width: 1000,
    height: 1300,
    runs: [
      ...extra,
      run(`Body text on page ${number}.`, 80, 400, { w: 300 }),
      run('Course Manual', 80, 1200, { w: 150 }),
      run(`Confidential 2018 Workday, Inc. ${number}`, 80, 1230, { w: 400 }),
    ],
    images: [],
  });
  const first = body(1, [
    { ...run('SPACED HEADING', 441, 60, { size: 14, w: 118, color: 'rgb(200, 0, 0)' }), spacing: 1.5 },
    run('Text inside a shaded box that wraps after quite a few words here', 200, 100, { w: 700 }),
    run('onto a second line in the box.', 200, 120, { w: 400 }),
    run('Objectives ......................................... 23', 80, 200, { w: 840 }),
    run('Name:', 80, 240, { w: 60, bold: true }),
    run('City, State', 780, 240, { w: 100 }),
    run('•', 80, 300, { w: 6 }),
    run('Bullet item that is long enough to wrap onto the following line', 100, 300, { w: 840 }),
    run('and continues under the text.', 100, 320, { w: 400 }),
    { ...run('Text that Word draws wider', 80, 360, { w: 270 }), vx: 80, vw: 270, fw: 300, sw: 5 },
  ]);
  first.images = [
    { src: 'box.png', x: 150, y: 84, w: 700, h: 88, crop: { left: 0, top: 0.25, right: 0.5, bottom: 0.25 } },
    { src: 'figure.png', x: 80, y: 600, w: 400, h: 200, crop: null },
  ];
  return [first, body(2), { ...body(3), width: 1300, height: 1000 }];
}

test('zip files round-trip and have correct checksums', async () => {
  assert.equal(crc32(new TextEncoder().encode('123456789')), 0xcbf43926);
  const big = 'word '.repeat(2000);
  const zip = await createZip([
    { name: 'a.xml', data: big },
    { name: 'media/image1.png', data: PNG, store: true },
    { name: 'empty.txt', data: '' },
  ]);
  assert.ok(zip.length < big.length, 'text is compressed');
  const entries = await unzip(zip);
  assert.deepEqual([...entries.keys()], ['a.xml', 'media/image1.png', 'empty.txt']);
  assert.equal(new TextDecoder().decode(entries.get('a.xml')), big);
  assert.deepEqual(entries.get('media/image1.png'), PNG);
});

test('xml text is escaped and control characters are removed', () => {
  assert.equal(escapeXml('a < b & "c" >\u0001\u000b'), 'a &lt; b &amp; &quot;c&quot; &gt;');
});

test('docx: one section per page, with real footers', async () => {
  const { bytes, failedImages } = await buildDocx(samplePages(), { loadImage });
  assert.equal(failedImages, 0);
  const files = await unzipText(bytes);
  const xml = files.get('word/document.xml');

  for (const name of ['[Content_Types].xml', '_rels/.rels', 'word/styles.xml', 'word/settings.xml', 'word/_rels/document.xml.rels']) {
    assert.ok(files.has(name), name);
  }
  // 3 pages -> 3 sections (2 inside paragraphs + the final one), 3 footer parts.
  assert.equal(xml.match(/<w:sectPr>/g).length, 3);
  assert.equal(xml.match(/<w:pgSz /g).length, 3);
  assert.match(xml, /<w:pgSz w:w="15000" w:h="19500"\/>/);
  assert.match(xml, /<w:pgSz w:w="19500" w:h="15000" w:orient="landscape"\/>/);
  for (const number of [1, 2, 3]) {
    const footer = files.get(`word/footer${number}.xml`);
    assert.match(footer, /Course Manual/);
    assert.match(footer, new RegExp(`Confidential 2018 Workday, Inc\\. ${number}<`));
  }
  assert.doesNotMatch(xml, /Course Manual|Confidential 2018/);
  // Body ends where the footer starts in the original (y=1200 of 1300 -> 100px = 1500 twips).
  assert.match(xml, /w:bottom="1500"/);
});

test('docx: text formatting matches the original layout', async () => {
  const files = await unzipText((await buildDocx(samplePages(), { loadImage })).bytes);
  const xml = files.get('word/document.xml');
  const paragraphOf = (text) => {
    const found = xml.split('<w:p>').find((part) => part.includes(text));
    assert.ok(found, `paragraph not found: ${text}
${[...xml.matchAll(/<w:t[^>]*>([^<]*)</g)].map((m) => m[1]).join(' | ')}`);
    return found;
  };

  // Letter-spaced heading: colour, spacing before size (schema order), centred.
  const heading = paragraphOf('SPACED HEADING');
  assert.match(heading, /<w:jc w:val="center"\/>/);
  assert.match(heading, /<w:color w:val="C80000"\/><w:spacing w:val="2[23]"\/><w:sz w:val="21"\/>/);

  // Exact line height copied from the original (20px pitch -> 300 twips), wraps inside the box.
  const boxed = paragraphOf('Text inside a shaded box that wraps after quite a few words here onto a second line in the box.');
  assert.match(boxed, /w:line="300" w:lineRule="exact"/);
  assert.match(boxed, /<w:ind [^>]*w:right="\d+"/);

  // Table of contents: dotted right tab instead of rows of dots.
  const toc = paragraphOf('Objectives');
  assert.match(toc, /<w:tab w:val="right" w:leader="dot" w:pos="\d+"\/>/);
  assert.match(toc, /Objectives<\/w:t><w:tab\/><w:t xml:space="preserve">23</);

  // Right-aligned column.
  const columns = paragraphOf('City, State');
  assert.match(columns, /<w:tab w:val="right" w:pos="\d+"\/>/);
  assert.match(columns, /<w:b\/>/);

  // Hanging indent: bullet at 80px, wrapped lines at 100px (20px = 300 twips).
  const bullet = paragraphOf('Bullet item');
  assert.match(bullet, /<w:ind w:left="300" w:hanging="300"/);

  // Wider substitute font: characters condensed to 90%.
  assert.match(paragraphOf('Text that Word draws wider'), /<w:w w:val="90"\/>/);
});

test('docx: images float behind text or sit inline, embedded once', async () => {
  const requested = [];
  const { bytes } = await buildDocx(samplePages(), {
    loadImage: async (src, crop) => {
      requested.push(src);
      return { data: PNG, crop };
    },
  });
  const files = await unzipText(bytes);
  const xml = files.get('word/document.xml');

  assert.deepEqual(requested.sort(), ['box.png', 'figure.png']);
  assert.ok(files.has('word/media/image1.png') && files.has('word/media/image2.png'));
  assert.match(files.get('[Content_Types].xml'), /Extension="png"/);

  // The box floats behind the text, anchored to the heading above it (y=60).
  const anchors = xml.match(/<wp:anchor [^>]*>.*?<\/wp:anchor>/g);
  assert.equal(anchors.length, 1);
  assert.match(anchors[0], /behindDoc="1"/);
  assert.match(anchors[0], new RegExp(`relativeFrom="page"><wp:posOffset>${150 * 9525}<`));
  assert.match(anchors[0], new RegExp(`relativeFrom="paragraph"><wp:posOffset>${(84 - 60) * 9525}<`));
  // The loader did not crop, so Word gets the crop.
  assert.match(anchors[0], /<a:srcRect t="25000" r="50000" b="25000"\/>/);
  assert.ok(xml.split('<w:p>').find((part) => part.includes('SPACED HEADING')).includes('<wp:anchor'));

  // The stand-alone figure is an inline picture.
  assert.equal(xml.match(/<wp:inline /g).length, 1);
});

test('docx: survives missing images and empty input', async () => {
  const broken = await buildDocx(samplePages(), {
    loadImage: async () => {
      throw new Error('network down');
    },
  });
  assert.equal(broken.failedImages, 2);
  assert.match((await unzipText(broken.bytes)).get('word/document.xml'), /SPACED HEADING/);

  const notAnImage = await buildDocx(samplePages(), { loadImage: async () => ({ data: new Uint8Array([1, 2, 3]) }) });
  assert.equal(notAnImage.failedImages, 2);

  const empty = await buildDocx([]);
  assert.ok((await unzipText(empty.bytes)).get('word/document.xml').includes('<w:body>'));
});
