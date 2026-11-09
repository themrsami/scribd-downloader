<p align="center">
  <img src="assets/scribd.svg" alt="Scribd" width="200">
</p>

<h1 align="center">Scribd Downloader</h1>

<p align="center">
  <b>Download Scribd documents as PDF, TXT or DOCX for free - Fast, automated, and runs in background!</b>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  </a>
  <a href="https://pypi.org/project/selenium/">
    <img src="https://img.shields.io/badge/Selenium-4.0+-green?style=for-the-badge&logo=selenium&logoColor=white" alt="Selenium 4.0+">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-orange?style=for-the-badge" alt="MIT License">
  </a>
</p>

<p align="center">
  <a href="https://buymeacoffee.com/mrsami">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee">
  </a>
  <a href="https://github.com/sponsors/fullstackusama">
    <img src="https://img.shields.io/badge/Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white" alt="GitHub Sponsors">
  </a>
  <a href="https://github.com/fullstackusama/scribd-downloader/stargazers">
    <img src="https://img.shields.io/github/stars/fullstackusama/scribd-downloader?style=for-the-badge&logo=github" alt="GitHub Stars">
  </a>
</p>

---

## Features

- **One-click download** - Just paste the Scribd URL and get your PDF
- **PDF, TXT and editable DOCX** - Text and Word files are rebuilt from Scribd's own HTML, keeping fonts, sizes, colours, bold/italic, lists, columns and images
- **Bulk download** - Pass several links, or a text file of links, in one run
- **Any Scribd link** - Works with `/document/...` and legacy `/doc/...` links, locale sites (`de.`, `es.`, `fr.` ...), links without `www` or a title, and private shared links with `secret_password`
- **Runs in background** - Headless Chrome, no browser window pops up
- **No scrolling required** - Loads Scribd page data directly instead of simulating page-by-page scrolling
- **Clean PDFs** - No cookie banners, toolbars, or watermarks
- **Bounded-memory export** - Keeps only a configurable batch of fully loaded pages in Chrome at once
- **Disk-spooled merge** - Writes each rendered page to temporary storage before combining the final PDF with `pypdf`
- **Large document support** - Verified with image-heavy documents containing up to 2,552 pages
- **Better math rendering** - Preserves Scribd layout classes needed by equations and SVG content
- **Exact pagination** - Validates that every Scribd page produces exactly one PDF sheet
- **Dynamic page size** - Detects each rendered page's dimensions instead of forcing one fixed sheet size
- **Auto filename** - Files named after the document URL automatically (safe on Windows, macOS and Linux)
- **Clear errors** - If Scribd shows no pages, the script says why (bot check, blocked IP, deleted document, wrong password) and saves a screenshot
- **No login required** - Works without Scribd account
- **Tested** - 119 offline unit tests plus a live end-to-end test (see [Testing](#testing))

---

## Requirements

- Python 3.10 or higher
- Google Chrome browser installed
- Chrome WebDriver (auto-managed by Selenium)

---

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/fullstackusama/scribd-downloader.git
   cd scribd-downloader
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

---

## Usage

1. **Run the script**
   ```bash
   python scribd-downloader.py
   ```

2. **Paste the Scribd document URL** when prompted:
   ```
   Input link Scribd: https://www.scribd.com/document/123456789/Document-Title
   ```

   Legacy Scribd URLs also work:
   ```
   Input link Scribd: https://www.scribd.com/doc/123456789/Document-Title
   ```

   So do locale sites, links without `www` or a title, and private shared links:
   ```
   Input link Scribd: https://de.scribd.com/document/123456789/Document-Title
   Input link Scribd: scribd.com/document/123456789
   Input link Scribd: https://www.scribd.com/document/123456789/Title?secret_password=AbC123
   ```
   The `secret_password` is passed to Scribd but always printed as `REDACTED`.

3. **Wait for the download** - The script will:
   - Open the document in headless Chrome
   - Load document pages directly in bounded batches
   - Release each batch from Chrome after printing to control memory use
   - Remove unwanted elements (toolbars, cookie banners)
   - Spool individual pages to temporary storage and merge the final PDF
   - Save the PDF in the current directory

4. **Done!** Your PDF will be saved with the document name from the URL.

### Command-line options

Running without arguments works exactly as before (it asks for a link). You can also pass everything on the command line:

```bash
# One document
python scribd-downloader.py https://www.scribd.com/document/123456789/Document-Title

# Several documents in one run (bulk download)
python scribd-downloader.py URL1 URL2 URL3

# A text file with one link per line ('#' lines are ignored)
python scribd-downloader.py --file links.txt

# Choose formats and an output folder
python scribd-downloader.py URL --format pdf,docx -o downloads
python scribd-downloader.py --file links.txt --format all
```

| Option | Description |
|--------|-------------|
| `URL ...` | One or more Scribd links. If none are given (and no `--file`), you are prompted. |
| `-f`, `--file PATH` | Text file with links, one per line. `-` reads from standard input. Can be repeated. |
| `--format FORMATS` | Comma-separated: `pdf`, `txt`, `docx`, or `all`. Default: `pdf`. |
| `-o`, `--output-dir DIR` | Folder for the output files (created if missing). Default: current folder. |

Bulk downloads run one document at a time (each Chrome instance can use 1.5-2.5 GB on large documents):

- Duplicate links (same document id) are downloaded once.
- Two documents with the same title get the document id appended (`Report.pdf`, `Report-222.pdf`).
- If one document fails, the others still download. A summary is printed at the end and the exit code is `1` if anything failed.

---

## Export Formats

| Format | How it is made | Best for |
|--------|----------------|----------|
| **PDF** | Chrome prints each Scribd page exactly as displayed | A faithful copy of the document |
| **TXT** | Text read from Scribd's page HTML, rebuilt into lines and paragraphs, with `===== Page N =====` markers | Searching, copying, feeding into other tools |
| **DOCX** | Same text, written to Word with the original fonts, sizes, colours, bold/italic, letter spacing, indents (including hanging indents for bullets), right-aligned columns (tab stops), lists and cropped images. One Word page per Scribd page, with the same paper size, margins, line heights and gaps, so the page count matches the original | Editing the document |

Scribd renders every page as HTML: each piece of text is a positioned element carrying the uploaded document's font, size and colour, and figures are `<img>` tags (often one image reused as a sprite with a CSS clip). The TXT and DOCX exports read that HTML directly instead of converting the PDF, so the text is exact rather than OCR'd or guessed from a PDF.

How the DOCX/TXT export handles tricky pages:

| What Scribd does | What the export does |
|------------------|----------------------|
| Stacks text and image layers. Edited PDFs keep old text in a lower layer, hidden under an image patch | Hidden text is dropped, so it does not appear twice. Text under an opaque (JPEG) patch is always hidden; under a PNG it is dropped only when newer text in a higher layer replaces it. Full-page scans keep their OCR text. |
| Draws fill-in blanks (`in ____.`) as 1px images | Turns them into `____` in the text line |
| Draws bullets as tiny images, in a tiny symbol font, or as Private Use Area characters (`U+E000`) that normal fonts cannot show | Turns them into normal `•` bullets with hanging indents |
| Puts text on top of boxes and shading, places icons beside text, builds diagrams from several images side by side, draws rules as 1px images | Floats those images at their original spot, anchored to the paragraph next to them so they move with their text, instead of stacking them between paragraphs |
| Repeats a running title, copyright line and page number at the bottom of every page | Puts them in the real Word footer of each page, so they can never be pushed onto an extra page |
| Draws table-of-contents dot leaders as rows of dots | Uses a right tab stop with a dot leader, like a real Word TOC |
| Uses CSS `letter-spacing` for spaced-out headings | Copies it to Word character spacing (small kerning tweaks are ignored) |
| Embeds the document's own fonts, which Word does not have | Measures each line in the browser with Word's substitute font and condenses the characters (Word "character scale") when the substitute is wider, so lines wrap where they did in the original |

The result is checked by opening the DOCX in Microsoft Word. The 307-page test document gives **exactly 307 Word pages** (before these fixes: 324), and the test documents keep their page counts too (2 → 2, 8 → 8).

Limitations:

- Scribd's HTML positions each piece of text, it does not store paragraphs, so paragraphs and lists are rebuilt from spacing, indentation and fonts. Simple documents come out very close to the original; multi-column magazine layouts and tables may need touch-ups in Word.
- Word draws text with the fonts installed on your computer. Character scaling keeps line widths close, but glyph shapes and heights still follow the substitute font, so very tight layouts can look slightly different.
- Scanned documents that only contain page images have no text to extract. The DOCX will contain the page images; TXT will be mostly empty (no OCR is done).
- Spreadsheet formulas and other source-file data are never sent to the browser by Scribd, so they cannot be recovered.
- DOCX export downloads the page images; if an image cannot be downloaded, the DOCX is still written and a warning is printed.

---

## Example Output

```text
$ python scribd-downloader.py
Input link Scribd: https://www.scribd.com/document/903361807/WorkdaySimpleIntegrations-EIB-31v2

Link embed: https://www.scribd.com/embeds/903361807/content
Output filename: WorkdaySimpleIntegrations-EIB-31v2.pdf

Starting Chrome browser...
Viewer ready: 316 page(s) detected.
Cookie dialogs hidden.
Top toolbar removed.
Bottom toolbar removed.
Adjusted 1 scroll containers for print.
Print CSS injected.

Saving PDF as: WorkdaySimpleIntegrations-EIB-31v2.pdf
  Export mode: Individual document pages
  Margins: None
  Headers/Footers: Disabled
  ChromeDriver command timeout: 600s
Exporting 316 document pages in bounded batches of 8...
  Loading page batch 1-8/316...
  Page 1/316 1002x1296px -> 10.438"x13.500"
    OK: exactly 1 PDF sheet
  ...
Merging 316 disk-spooled PDF pages...
PDF saved successfully to: C:\Users\...\WorkdaySimpleIntegrations-EIB-31v2.pdf
Browser closed.
```

---

## PDF Settings

| Setting | Value |
|---------|-------|
| Page Size | Detected dynamically from Scribd's rendered page |
| Margins | None (0) |
| Headers/Footers | Disabled |
| Background Graphics | Enabled |

---

## How It Works

1. **URL Conversion** - Validates the link (only `scribd.com` and its subdomains) and converts it to the embeddable format, keeping any `secret_password`
2. **Headless Browser** - Opens Chrome in background (invisible) and waits until the viewer has created its page list (instead of a fixed delay)
3. **Batched Page Loading** - Loads a small group of Scribd pages and their images directly without scrolling; for TXT/DOCX, reads each batch's text and images from the HTML
4. **Cleanup** - Removes toolbars, cookie banners, and overlays while preserving Scribd layout classes
5. **Per-page Export** - Detects each page's rendered size and prints exactly one PDF sheet through Chrome DevTools Protocol
6. **Memory Release** - Removes the completed batch from Chrome and requests garbage collection
7. **Disk-spooled Merge** - Combines the temporary one-page PDFs into the final document with `pypdf`
8. **Auto Close** - Browser closes automatically after saving
9. **TXT / DOCX** - Rebuilds lines, paragraphs, lists and columns from the extracted text positions and writes the requested files

---

## Benchmarks

Reference results from one Windows machine are shown below. Performance varies with network speed, document complexity, CPU, RAM, Chrome version, and storage speed.

| Document | Pages | Total Time | Output Size | Blank Pages | Peak Combined RAM |
|----------|------:|-----------:|------------:|------------:|------------------:|
| CSS Solved Past Papers | 359 | 58.36 seconds | 228.49 MB | 0 | 1.47 GB |
| Manual de Servicio MX-305 | 2,552 | 14 minutes 12 seconds | 237.28 MB | 0 | 2.46 GB |

The batch size was `8` in both tests. Long documents still take time because Chrome must print every page individually, but fully loaded browser content is kept to one batch at a time.

---

## Troubleshooting

### "ChromeDriver not found" error
The script uses Selenium Manager to auto-download ChromeDriver. If you face issues:
```bash
pip install --upgrade selenium
```

### "Invalid Scribd URL"
Links from `de.scribd.com`, `es.scribd.com` and other locale sites, links without `www`, and links without a title are all supported. Only real document links work (`/document/<id>`, `/doc/<id>`); profile, search and book/audiobook pages are not documents.

### "No printable document pages were detected"
The script now waits for the Scribd viewer (up to `SCRIBD_DOCUMENT_READY_TIMEOUT`, default `30` seconds) and then explains what it saw:

| Reason shown | What to do |
|--------------|------------|
| Bot check (Cloudflare) | Common on VPS / data-centre IPs. Run from a home connection or residential proxy, or try `SCRIBD_HEADLESS=0`. |
| Access denied | Scribd blocked the network. Same as above. |
| Document not found | The document was deleted or made private. |
| `secret_password` may be wrong | Copy the full shared link again. |
| Viewer did not load any pages | Slow network: raise `SCRIBD_DOCUMENT_READY_TIMEOUT` (e.g. `90`). |

It also saves `scribd-debug-<id>.png` and `scribd-debug-<id>.html` in the output folder so you can see exactly what Scribd served.

### PDF not saving
- Ensure you have write permissions in the current directory
- Check if the Scribd URL is valid and accessible
- For very large documents, increase `SCRIBD_CDP_TIMEOUT` (default: `600`)

### Blank pages in PDF
- Some documents may have DRM protection
- Try increasing `SCRIBD_PAGE_LOAD_TIMEOUT` if page images load slowly
- If a document still renders incorrectly, try visible mode with `SCRIBD_HEADLESS=0`

### Very large documents
- Ensure the drive containing your temporary directory has enough free space for individual page PDFs
- Reduce `SCRIBD_EXPORT_BATCH_SIZE` if Chrome uses too much memory
- Increase `SCRIBD_PAGE_LOAD_TIMEOUT` when slow image assets time out
- Long documents still take time because each page is printed and validated separately

### Large, image-heavy, or math-heavy documents
You can tune the export with environment variables:

```powershell
$env:SCRIBD_CDP_TIMEOUT="900"
$env:SCRIBD_PAGE_LOAD_TIMEOUT="180"
$env:SCRIBD_EXPORT_BATCH_SIZE="4"
python scribd-downloader.py
```

Useful variables:

- `SCRIBD_CDP_TIMEOUT` - ChromeDriver command timeout in seconds for `Page.printToPDF`
- `SCRIBD_PAGE_LOAD_TIMEOUT` - Maximum direct page-loading time in seconds (default: `120`)
- `SCRIBD_EXPORT_BATCH_SIZE` - Maximum fully loaded pages kept in Chrome at once (default: `8`)
- `SCRIBD_DOCUMENT_READY_TIMEOUT` - Seconds to wait for the Scribd viewer to show its pages (default: `30`)
- `SCRIBD_HEADLESS=0` - Run with a visible browser when debugging rendering issues locally

---

## Testing

The project has two kinds of tests:

- **Unit tests** (119) run offline in under a second. No Chrome, no internet: Selenium, the network and image downloads are replaced by small fakes.
- **End-to-end test** (1) downloads real documents from scribd.com in every format. It needs Chrome and internet, so it is skipped unless you turn it on.

### Run the tests

```bash
pip install -r requirements-dev.txt

# Unit tests
python -m pytest

# Live end-to-end test (Chrome + internet)
SCRIBD_E2E=1 python -m pytest -m e2e -v
```

On Windows PowerShell, use `$env:SCRIBD_E2E="1"; python -m pytest -m e2e -v`. To test other documents, set `SCRIBD_E2E_URLS` to space-separated links.

### What is covered

| Test file | Tests | What it checks |
|-----------|------:|----------------|
| `tests/test_urls.py` | 36 | Accepts `www`, locale (`de.`/`es.`/`fr.`), no-`www`, no-scheme, `http`, uppercase, no-title, `/doc/` and `/embeds/` links. Rejects lookalike domains (`notscribd.com`, `evil-scribd.com`, `scribd.com.evil.com`) and non-document pages. Keeps and URL-encodes `secret_password`, redacts it in output. Safe filenames. |
| `tests/test_cli.py` | 10 | Format parsing, default arguments match the old behaviour, links from arguments / files / stdin / prompt, duplicate removal, name clashes, bulk run continues after a failure with the right summary and exit code. |
| `tests/test_diagnostics.py` | 13 | Waits until the page count is stable, survives script errors while loading, times out cleanly. Recognises Cloudflare checks, blocked access, missing documents (Scribd's real "Document Not Found" text), bad `secret_password`, private documents and login walls. Saves debug screenshot + HTML and never prints the password. |
| `tests/test_layout.py` | 34 | Rebuilding text from positioned HTML: joining runs, spaces vs punctuation, non-breaking spaces, bullets drawn in tiny fonts or as tiny sprite images, column gaps to tabs, wrapped lines, hyphenation, list items, bold lead-ins, headings, centring, styles, reading order of text and images, colour parsing. |
| `tests/test_export_files.py` | 6 | TXT output exactly. DOCX keeps fonts, sizes, bold/italic, colours, centring, right-aligned tab stops, enlarged bullets, one section per page size (portrait/landscape), cropped sprite images. DOCX is still written when images fail to download or are invalid. |
| `tests/test_fidelity.py` | 9 | Hidden text under JPEG/PNG patches in edited PDFs (and kept for full-page scans and text inside boxes), fill-in blanks drawn as images, boxes placed behind text, letter spacing only for real spaced-out text. Word layout: one section per Scribd page, margins from the text area, exact line heights, hanging indents, wrapping inside boxes, floating "behind text" pictures anchored to their paragraph. |
| `tests/test_page_fit.py` | 11 | Keeping Word pages identical to Scribd pages: repeating footers detected (one-off text near the bottom is not) and written to the real Word footer, table-of-contents dot leaders as dotted right tabs, line widths including spaces between runs, condensing (never stretching) wider substitute fonts, run properties in the order Word requires, side-by-side images and rules floating, rules anchored under their text, Private Use Area bullets. |
| `tests/test_e2e.py` | 1 | Bulk download of real documents from a links file with `--format all`: a `de.scribd.com` link (8 pages) and a no-`www`, no-scheme link (2 pages, columns and bullets). Checks PDF page count, one TXT page marker per PDF page, and real text in TXT and DOCX. |

### Latest results

Run on Windows 11, Python 3.14, Chrome 156, Selenium 4.51:

| Check | Result |
|-------|--------|
| Unit tests | **119 passed** in 0.8 s |
| End-to-end test | **1 passed** in 13 s (2 documents, PDF + TXT + DOCX) |
| DOCX page count in Microsoft Word | **Same as Scribd**: 2 → 2, 8 → 8, 307 → 307 |
| PDF regression vs. previous version | **Identical**: same page count, page sizes and text on every page (8-page and 2-page documents) |
| Large document, `--format all` | **307 pages in 2 min 40 s**: 307-page PDF (24 MB), TXT with 307 page markers (43,000 words), DOCX (4 MB) that opens in Word as exactly 307 pages, no failed images |
| Missing document | Exit code `1`, reason "The document was not found", debug screenshot and HTML saved |

---

## Contributing

Contributions are welcome! Feel free to:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## Support the Project

If you find this tool useful, consider supporting its development:

<p align="center">
  <a href="https://buymeacoffee.com/mrsami">
    <img src="assets/buymeacoffee.svg" alt="Buy Me A Coffee" width="40" height="40">
  </a>
</p>

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Disclaimer

This tool is for educational purposes only. Please respect copyright laws and Scribd's Terms of Service. Only download documents you have the right to access.

---

<p align="center">
  Made with ❤️ by <a href="https://github.com/fullstackusama">Usama Nazir</a>
</p>

<p align="center">
  If you find this useful, please consider giving it a ⭐
</p>
