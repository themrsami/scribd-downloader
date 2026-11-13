# Scribd Downloader - Chrome extension

The same downloader as [`scribd-downloader.py`](../scribd-downloader.py), as a Chrome extension. No Python, no terminal: open a Scribd document and click **Download**.

It is not affiliated with Scribd.

## Features

Everything the Python downloader does:

- **PDF, Word and Text** - an exact PDF copy, an editable `.docx`, or plain `.txt`. Pick one or several.
- **One click** - a Download button appears on Scribd document pages, and in the toolbar popup.
- **Bulk download** - paste many links at once. Duplicates are skipped, a failed document does not stop the rest.
- **Any Scribd link** - `www`, locale sites (`de.`, `es.`, `fr.` ...), links without `https://` or a title, old `/doc/` links, and private shared links with `secret_password`.
- **Clear errors** - if a document does not load, it says why: deleted document, "verify you are human" check, blocked network, wrong password, or login page.
- **Progress, cancel and retry** - page-by-page progress in the popup and on the toolbar icon.
- **No login, no server** - everything runs in your own browser. Nothing is sent anywhere except to Scribd.

## Install (developer mode)

1. Download or clone this repository.
2. Open `chrome://extensions` in Chrome (or Edge, Brave).
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** and choose the `extension` folder.
5. Pin **Scribd Downloader** to the toolbar.

Needs Chrome 120 or newer.

## Use

**One document:** open it on Scribd and click the **Download** button at the bottom right of the page. Or click the toolbar icon, choose the formats, and press **Download**.

**Several documents:** click the toolbar icon, paste the links (one per line) and press **Download**. They are downloaded one after another.

Files are saved to your Downloads folder, named after the document.

## The "started debugging this browser" bar

While a download runs, Chrome shows a bar saying *"Scribd Downloader started debugging this browser"*. This is expected.

Chrome only lets an extension print a page to PDF through its debugger interface, and it always shows this bar when that interface is in use. The extension uses it only on the background tab it opens for the document, and the bar goes away when the download ends. Pressing **Cancel** on the bar stops the download.

## How it works

It follows the same steps as the Python script, using the browser you already have instead of launching a second one:

1. Opens the document's embed page in a background tab.
2. Loads pages in batches of 8, prints each page to exactly one PDF sheet, then frees the batch to keep memory low.
3. For Word and Text, reads the text and images from Scribd's page HTML and rebuilds lines, paragraphs, lists and columns.
4. Merges the PDF sheets ([pdf-lib](https://pdf-lib.js.org/), MIT, bundled in `vendor/`), writes the `.docx` directly, and saves the files.

| Folder / file | What it is |
|---------------|------------|
| `manifest.json` | Extension manifest (Manifest V3) |
| `background.js` | Download queue, saving files |
| `lib/engine.js` | Runs one document: load, print, extract |
| `lib/page-scripts.js` | Code that runs inside the Scribd page |
| `lib/layout.js` | Rebuilds paragraphs and lists (port of the Python layout code) |
| `lib/docx.js`, `lib/zip.js` | Writes the Word file |
| `lib/url.js`, `lib/diagnostics.js` | Link parsing, error explanations |
| `popup/` | Toolbar popup |
| `content/button.js` | Download button on Scribd pages |
| `tools/make_icons.py` | Regenerates the icons |

### Permissions

| Permission | Why |
|------------|-----|
| `debugger` | Print pages to PDF (see above) |
| `downloads` | Save the finished files |
| `storage` | Remember your format choice and the download list |
| `offscreen` | Hand finished files to Chrome's download manager |
| `scribd.com`, `scribdassets.com` | Open documents and fetch their images |

## Tests

Unit tests need only Node 20+ (no install step):

```bash
cd extension
npm test
```

26 tests cover link parsing, bulk planning, error explanations, the layout engine, the ZIP writer and the Word writer.

Checked against the Python downloader, with the extension loaded in real Chrome:

| Check | Result |
|-------|--------|
| Layout engine vs. Python, same page data (317 pages, 3 documents) | Identical text, paragraphs and footers |
| 2-page document, all formats | PDF text and page sizes identical, TXT identical, Word text identical |
| 307-page document, all formats | Done in 80 s. PDF identical to Python's (pages, sizes, text, images). Word file opens in Microsoft Word as exactly 307 pages |
| Bulk: 4 links (one invalid, one deleted document) | Valid ones saved, clear message for the other two |
| Cancel mid-download | Stops within a page, background tab closed |
| Download button on a live Scribd page | PDF saved |

## Limitations

Same as the Python downloader's Word and Text export:

- Scanned documents have no text layer, so Text is mostly empty (no OCR).
- Complex layouts (multi-column magazines, tables) may need touch-ups in Word.
- Word uses the fonts installed on your computer, so very tight layouts can look slightly different.

Also:

- Downloads run one document at a time.
- Keep Chrome open until a download finishes. Closing the browser, or the background tab, stops it.

## Disclaimer

For educational purposes only. Respect copyright law and Scribd's Terms of Service, and only download documents you have the right to access.
