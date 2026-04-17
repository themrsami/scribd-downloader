<p align="center">
  <img src="assets/scribd.svg" alt="Scribd" width="200">
</p>

<h1 align="center">Scribd Downloader</h1>

<p align="center">
  <b>Download Scribd documents as PDF for free - Fast, automated, and runs in background!</b>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/Python-3.7+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.7+">
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
  <a href="https://github.com/sponsors/themrsami">
    <img src="https://img.shields.io/badge/Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white" alt="GitHub Sponsors">
  </a>
  <a href="https://github.com/themrsami/scribd-downloader/stargazers">
    <img src="https://img.shields.io/github/stars/themrsami/scribd-downloader?style=for-the-badge&logo=github" alt="GitHub Stars">
  </a>
</p>

---

## Features

- **One-click download** - Just paste the Scribd URL and get your PDF
- **Supports both Scribd URL styles** - Works with `/document/...` and legacy `/doc/...` links
- **Runs in background** - Headless Chrome, no browser window pops up
- **Fast processing** - Optimized scrolling and minimal wait times
- **Clean PDFs** - No cookie banners, toolbars, or watermarks
- **Large file friendly** - Uses a longer ChromeDriver timeout and streamed PDF export for big image-based documents
- **Better math rendering** - Waits for fonts and render stability before printing, and preserves Scribd layout classes needed by equations/SVG
- **Better pagination** - Uses Scribd's real page wrappers to avoid extra trailing pages
- **Dynamic page size** - Detects the rendered Scribd page size instead of forcing one fixed sheet size
- **Auto filename** - PDF named after the document URL automatically
- **No login required** - Works without Scribd account

---

## Requirements

- Python 3.7 or higher
- Google Chrome browser installed
- Chrome WebDriver (auto-managed by Selenium)

---

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/themrsami/scribd-downloader.git
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

3. **Wait for the download** - The script will:
   - Open the document in headless Chrome
   - Scroll through all pages to load content
   - Remove unwanted elements (toolbars, cookie banners)
   - Save as PDF in the current directory

4. **Done!** Your PDF will be saved with the document name from the URL.

---

## Example Output

```text
$ python scribd-downloader.py
Input link Scribd: https://www.scribd.com/document/903361807/WorkdaySimpleIntegrations-EIB-31v2

Link embed: https://www.scribd.com/embeds/903361807/content
Output filename: WorkdaySimpleIntegrations-EIB-31v2.pdf

Starting Chrome browser...
Cookie dialogs hidden.
Found 316 pages, scrolling...
  Scrolled 10/316 pages...
  Scrolled 20/316 pages...
  ...
  Detected 617 pages after lazy loading, continuing...
All 617 pages loaded.
Top toolbar removed.
Bottom toolbar removed.
Adjusted 1 scroll containers for print.
Print CSS injected.
Render settle reached its time budget; continuing with best effort.

Saving PDF as: WorkdaySimpleIntegrations-EIB-31v2.pdf
  Page size: 10.44" x 13.50" (from .outer_page)
  Margins: None
  Headers/Footers: Disabled
  ChromeDriver command timeout: 600s
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

1. **URL Conversion** - Converts Scribd document URL to embeddable format
2. **Headless Browser** - Opens Chrome in background (invisible)
3. **Page Loading** - Scrolls through all pages to trigger lazy-loading
4. **Cleanup** - Removes toolbars, cookie banners, and overlays while preserving Scribd layout classes
5. **Render Stabilization** - Waits for fonts, images, and page layout to settle before printing
6. **Dynamic PDF Generation** - Detects the rendered page size and uses Chrome DevTools Protocol to generate the PDF directly
7. **Auto Close** - Browser closes automatically after saving

---

## Troubleshooting

### "ChromeDriver not found" error
The script uses Selenium Manager to auto-download ChromeDriver. If you face issues:
```bash
pip install --upgrade selenium
```

### PDF not saving
- Ensure you have write permissions in the current directory
- Check if the Scribd URL is valid and accessible
- For very large documents, increase `SCRIBD_CDP_TIMEOUT` (default: `600`)

### Page counter looks too high while scrolling
- Scribd creates extra internal page elements while lazy-loading
- The scrolling progress is only a loading indicator and can be higher than the final PDF page count

### Blank pages in PDF
- Some documents may have DRM protection
- Try increasing `SCRIBD_SCROLL_DELAY` if pages are not loading completely
- If a document still renders incorrectly, try visible mode with `SCRIBD_HEADLESS=0`

### Large, image-heavy, or math-heavy documents
You can tune the export with environment variables:

```powershell
$env:SCRIBD_CDP_TIMEOUT="900"
$env:SCRIBD_RENDER_SETTLE_TIMEOUT="45"
$env:SCRIBD_SCROLL_DELAY="0.2"
python scribd-downloader.py
```

Useful variables:

- `SCRIBD_CDP_TIMEOUT` - ChromeDriver command timeout in seconds for `Page.printToPDF`
- `SCRIBD_RENDER_SETTLE_TIMEOUT` - Maximum time to wait for fonts/images/layout to settle before exporting
- `SCRIBD_SCROLL_DELAY` - Delay between page scrolls when forcing lazy-loaded pages to render
- `SCRIBD_PDF_STREAM_CHUNK_SIZE` - Chunk size used when reading a streamed PDF response from Chrome
- `SCRIBD_HEADLESS=0` - Run with a visible browser when debugging rendering issues locally

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
  Made with ❤️ by <a href="https://github.com/themrsami">Usama Nazir</a>
</p>

<p align="center">
  If you find this useful, please consider giving it a ⭐
</p>
