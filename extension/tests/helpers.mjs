// Shared helpers for the extension's unit tests (run with: node --test extension/tests).

/** One extracted text run, in the shape the Scribd page script returns. */
export function run(text, x, y, extra = {}) {
  const size = extra.size ?? 16;
  return {
    text,
    x,
    y,
    w: extra.w ?? text.length * size * 0.5,
    size,
    font: 'Arial',
    color: 'rgb(0, 0, 0)',
    bold: false,
    italic: false,
    ...extra,
  };
}

/** A tiny valid PNG (1x1, red). */
export const PNG = Uint8Array.from(
  atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=='),
  (char) => char.charCodeAt(0)
);

/** Read every entry of a ZIP file made by lib/zip.js. Returns Map(name -> Uint8Array). */
export async function unzip(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const decoder = new TextDecoder();
  const entries = new Map();
  let offset = 0;
  while (view.getUint32(offset, true) === 0x04034b50) {
    const method = view.getUint16(offset + 8, true);
    const compressedSize = view.getUint32(offset + 18, true);
    const nameLength = view.getUint16(offset + 26, true);
    const extraLength = view.getUint16(offset + 28, true);
    const name = decoder.decode(bytes.subarray(offset + 30, offset + 30 + nameLength));
    const start = offset + 30 + nameLength + extraLength;
    let data = bytes.subarray(start, start + compressedSize);
    if (method === 8) {
      const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
      data = new Uint8Array(await new Response(stream).arrayBuffer());
    }
    entries.set(name, data);
    offset = start + compressedSize;
  }
  return entries;
}

export async function unzipText(bytes) {
  const decoder = new TextDecoder();
  const result = new Map();
  for (const [name, data] of await unzip(bytes)) result.set(name, decoder.decode(data));
  return result;
}
