"""
Scribd Document Downloader
==========================

A Selenium-based utility that loads a Scribd embed and saves it as a PDF.

Key behaviors:
1. Converts a Scribd document URL to the embed/content URL.
2. Opens the document in Chrome.
3. Scrolls through every page to trigger lazy loading.
4. Removes UI overlays without stripping layout classes needed for rendering.
5. Waits for fonts, images, and page geometry to settle.
6. Saves the PDF through Chrome DevTools Protocol with a larger timeout and
   stream-based PDF transfer for large documents.
"""

import base64
import os
import re
import shutil
import time
from urllib.parse import unquote, urlparse

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options


DEFAULT_CDP_TIMEOUT_SECONDS = int(os.getenv("SCRIBD_CDP_TIMEOUT", "600"))
DEFAULT_RENDER_SETTLE_TIMEOUT_SECONDS = int(
    os.getenv("SCRIBD_RENDER_SETTLE_TIMEOUT", "30")
)
DEFAULT_SCROLL_DELAY_SECONDS = float(os.getenv("SCRIBD_SCROLL_DELAY", "0.15"))
PDF_STREAM_CHUNK_SIZE = int(os.getenv("SCRIBD_PDF_STREAM_CHUNK_SIZE", str(1024 * 1024)))
HEADLESS_ENABLED = os.getenv("SCRIBD_HEADLESS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_PAPER_WIDTH_INCHES = 7.25
DEFAULT_PAPER_HEIGHT_INCHES = 10.5


def build_chrome_options():
    """Create Chrome options for reliable headless PDF generation."""
    options = Options()
    runtime_profile_dir = os.path.join(os.getcwd(), ".chrome-runtime-profile")
    shutil.rmtree(runtime_profile_dir, ignore_errors=True)
    os.makedirs(runtime_profile_dir, exist_ok=True)

    if HEADLESS_ENABLED:
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1600,2200")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument(f"--user-data-dir={runtime_profile_dir}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--force-color-profile=srgb")
    options.add_argument("--hide-scrollbars")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    return options, runtime_profile_dir


def convert_scribd_link(url):
    """
    Convert a Scribd document URL to the embed/content URL.

    Args:
        url: Standard Scribd URL such as
            https://www.scribd.com/document/123456789/Document-Title
            or https://www.scribd.com/doc/123456789/Document-Title

    Returns:
        The embeddable content URL, or "Invalid Scribd URL" if no document id
        can be extracted.
    """
    match = re.search(r"https://www\.scribd\.com/(?:document|doc)/(\d+)/", url)
    if not match:
        return "Invalid Scribd URL"

    return f"https://www.scribd.com/embeds/{match.group(1)}/content"


def get_filename_from_url(url):
    """
    Build an output filename from the last URL path segment.

    Args:
        url: Scribd document URL.

    Returns:
        Filename ending in ".pdf".
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    last_segment = path.split("/")[-1] if path else "scribd_document"
    return f"{unquote(last_segment)}.pdf"


def configure_command_timeout(driver, timeout_seconds):
    """
    Increase the Selenium HTTP timeout used for ChromeDriver commands.

    Large image-heavy documents can spend minutes inside Page.printToPDF before
    ChromeDriver responds, so the default 120 second timeout is too small.
    """
    executor = getattr(driver, "command_executor", None)
    if executor is None:
        return

    client_config = getattr(executor, "client_config", None)
    if client_config is None:
        client_config = getattr(executor, "_client_config", None)

    if client_config is not None:
        client_config.timeout = timeout_seconds


def hide_cookie_dialogs(driver):
    """Dismiss and remove common cookie, consent, and privacy banners."""
    driver.execute_script(
        """
        const closeButtonSelectors = [
            '[class*="cookie"] [class*="close"]',
            '[class*="cookie"] [class*="dismiss"]',
            '[class*="cookie"] button[aria-label*="close"]',
            '[class*="cookie"] button[aria-label*="Close"]',
            '[class*="consent"] [class*="close"]',
            '[class*="consent"] [class*="dismiss"]',
            '[class*="banner"] [class*="close"]',
            '[class*="banner"] [class*="dismiss"]',
            '[class*="notice"] [class*="close"]',
            '[class*="notice"] [class*="dismiss"]',
            'button[class*="close"]',
            'button[aria-label="Close"]',
            'button[aria-label="close"]',
            'button[aria-label="Dismiss"]',
            '[data-dismiss]',
            '[role="button"][class*="close"]'
        ];

        closeButtonSelectors.forEach((selector) => {
            try {
                document.querySelectorAll(selector).forEach((button) => button.click());
            } catch (error) {}
        });

        const cookieSelectors = [
            '[class*="cookie"]',
            '[class*="Cookie"]',
            '[class*="consent"]',
            '[class*="Consent"]',
            '[class*="gdpr"]',
            '[class*="GDPR"]',
            '[id*="cookie"]',
            '[id*="Cookie"]',
            '[id*="consent"]',
            '[id*="gdpr"]',
            '[class*="privacy-notice"]',
            '[class*="Privacy"]',
            '[class*="cookie-banner"]',
            '[class*="cookie-notice"]',
            '[class*="cookie-popup"]',
            '[class*="cookie-modal"]',
            '[class*="CookieConsent"]',
            '[class*="notice-banner"]',
            '.cc-window',
            '.cc-banner',
            '#onetrust-consent-sdk',
            '#onetrust-banner-sdk',
            '.evidon-banner',
            '.truste_box_overlay',
            '[class*="osano-cm"]',
            '[id*="osano"]'
        ];

        cookieSelectors.forEach((selector) => {
            try {
                document.querySelectorAll(selector).forEach((element) => element.remove());
            } catch (error) {}
        });

        document.querySelectorAll('*').forEach((element) => {
            try {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                const text = (element.innerText || '').toLowerCase();
                const fixedAtTop =
                    (style.position === 'fixed' || style.position === 'sticky') &&
                    rect.top < 100;

                if (
                    fixedAtTop &&
                    (
                        text.includes('cookie') ||
                        text.includes('privacy') ||
                        text.includes('consent') ||
                        text.includes('analytics') ||
                        text.includes('advertising') ||
                        text.includes('personalization')
                    )
                ) {
                    element.remove();
                }
            } catch (error) {}
        });
        """
    )


def scroll_through_pages(driver, scroll_delay_seconds):
    """
    Scroll through all detected pages until the page count stabilizes.

    Scribd lazily renders more page nodes while scrolling, so a single snapshot
    of "[class*='page']" is not always enough for long documents.
    """
    scrolled_count = 0
    stable_rounds = 0
    last_total_pages = -1

    while stable_rounds < 2:
        page_elements = driver.find_elements("css selector", "[class*='page']")
        total_pages = len(page_elements)

        if total_pages == 0:
            print("No page elements were detected.")
            return 0

        if total_pages == last_total_pages:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_total_pages = total_pages

        if scrolled_count == 0:
            print(f"Found {total_pages} pages, scrolling...")
        elif total_pages > scrolled_count:
            print(f"Detected {total_pages} pages after lazy loading, continuing...")

        for index in range(scrolled_count, total_pages):
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});",
                page_elements[index],
            )
            time.sleep(scroll_delay_seconds)

            if (index + 1) % 10 == 0:
                print(f"  Scrolled {index + 1}/{total_pages} pages...")

        scrolled_count = total_pages
        time.sleep(0.5)

    print(f"All {scrolled_count} pages loaded.")
    return scrolled_count


def prepare_document_for_print(driver):
    """
    Remove UI chrome and make the scroll containers printable.

    The old version removed the .document_scroller class entirely, which can
    break descendant CSS needed by math- and font-heavy documents. We keep the
    class and only override the few layout properties that interfere with print.
    """
    result = driver.execute_script(
        """
        const removed = { toolbarTop: false, toolbarBottom: false, containers: 0 };

        const toolbarTop = document.querySelector('.toolbar_top');
        if (toolbarTop) {
            toolbarTop.remove();
            removed.toolbarTop = true;
        }

        const toolbarBottom = document.querySelector('.toolbar_bottom');
        if (toolbarBottom) {
            toolbarBottom.remove();
            removed.toolbarBottom = true;
        }

        document.querySelectorAll('.document_scroller').forEach((element) => {
            element.setAttribute('data-scribd-print-root', 'true');
            element.style.position = 'static';
            element.style.top = 'auto';
            element.style.bottom = 'auto';
            element.style.left = 'auto';
            element.style.right = 'auto';
            element.style.overflow = 'visible';
            element.style.maxHeight = 'none';
            element.style.height = 'auto';
            element.style.margin = '0';
            element.style.padding = '0';
            removed.containers += 1;
        });

        return removed;
        """
    )

    if result["toolbarTop"]:
        print("Top toolbar removed.")
    if result["toolbarBottom"]:
        print("Bottom toolbar removed.")

    print(f"Adjusted {result['containers']} scroll containers for print.")


def inject_print_styles(driver):
    """Install conservative print CSS without hiding Scribd document content."""
    driver.execute_script(
        """
        const existing = document.getElementById('scribd-print-styles');
        if (existing) {
            existing.remove();
        }

        const style = document.createElement('style');
        style.id = 'scribd-print-styles';
        style.textContent = `
            [class*="cookie"],
            [class*="Cookie"],
            [class*="consent"],
            [class*="Consent"],
            [class*="gdpr"],
            [class*="privacy-notice"],
            [class*="notice-banner"],
            [id*="cookie"],
            [id*="consent"],
            [class*="osano-cm"],
            [id*="osano"] {
                display: none !important;
                visibility: hidden !important;
                opacity: 0 !important;
                height: 0 !important;
                overflow: hidden !important;
            }

            [data-scribd-print-root="true"],
            .document_scroller {
                position: static !important;
                top: auto !important;
                right: auto !important;
                bottom: auto !important;
                left: auto !important;
                overflow: visible !important;
                height: auto !important;
                max-height: none !important;
                margin: 0 !important;
                padding: 0 !important;
            }

            @media print {
                @page {
                    size: 7.25in 10.5in;
                    margin: 0;
                }

                html,
                body {
                    margin: 0 !important;
                    padding: 0 !important;
                    -webkit-print-color-adjust: exact !important;
                    print-color-adjust: exact !important;
                }

                .toolbar_top,
                .toolbar_bottom {
                    display: none !important;
                }

                [data-scribd-print-root="true"],
                .document_scroller {
                    position: static !important;
                    top: auto !important;
                    right: auto !important;
                    bottom: auto !important;
                    left: auto !important;
                    overflow: visible !important;
                    height: auto !important;
                    max-height: none !important;
                    margin: 0 !important;
                    padding: 0 !important;
                }

                .outer_page {
                    margin: 0 !important;
                    break-inside: avoid !important;
                    page-break-inside: avoid !important;
                    break-after: page !important;
                    page-break-after: always !important;
                }

                .outer_page:last-of-type {
                    break-after: auto !important;
                    page-break-after: auto !important;
                }

                mjx-container,
                .MathJax,
                .katex,
                math,
                svg {
                    visibility: visible !important;
                    overflow: visible !important;
                }
            }
        `;

        document.head.appendChild(style);
        """
    )

    print("Print CSS injected.")


def wait_for_render_stability(driver, timeout_seconds):
    """
    Wait for fonts, images, and page dimensions to settle before printing.

    This lowers the risk of exporting before math glyphs, SVG content, or web
    fonts finish rendering.
    """
    driver.set_script_timeout(timeout_seconds + 5)

    try:
        result = driver.execute_async_script(
            """
            const settleBudgetMs = arguments[0];
            const done = arguments[arguments.length - 1];
            const start = performance.now();
            let stableTicks = 0;
            let lastSample = '';

            function sample() {
                const pages = Array.from(document.querySelectorAll("[class*='page']"));
                const heights = pages.slice(0, 12).map((element) =>
                    Math.round(element.getBoundingClientRect().height)
                );
                const pendingImages = Array.from(document.images || []).filter(
                    (image) => !image.complete
                ).length;
                const busyNodes = document.querySelectorAll(
                    "[aria-busy='true'], [class*='loading'], [class*='spinner']"
                ).length;

                return JSON.stringify({
                    pageCount: pages.length,
                    heights,
                    pendingImages,
                    busyNodes
                });
            }

            function finish(timedOut) {
                done({
                    timedOut,
                    sample: lastSample || sample()
                });
            }

            function tick() {
                lastSample = sample();
                const parsed = JSON.parse(lastSample);
                const isBusy = parsed.pendingImages > 0 || parsed.busyNodes > 0;

                if (!isBusy && lastSample === window.__scribdLastRenderSample) {
                    stableTicks += 1;
                } else {
                    stableTicks = 0;
                }

                window.__scribdLastRenderSample = lastSample;

                if (stableTicks >= 2) {
                    finish(false);
                    return;
                }

                if (performance.now() - start >= settleBudgetMs) {
                    finish(true);
                    return;
                }

                requestAnimationFrame(() => setTimeout(tick, 200));
            }

            const fontsReady = document.fonts && document.fonts.ready
                ? document.fonts.ready.catch(() => undefined)
                : Promise.resolve();

            fontsReady.finally(() => {
                requestAnimationFrame(() => setTimeout(tick, 200));
            });
            """,
            int(timeout_seconds * 1000),
        )
    except WebDriverException as error:
        print(f"Render settle check failed; continuing with best effort: {error}")
        return

    if result.get("timedOut"):
        print("Render settle reached its time budget; continuing with best effort.")
    else:
        print("Document render settled before export.")


def detect_document_paper_size(driver):
    """
    Infer a paper size from the first rendered Scribd page.

    Scribd pages often render as absolutely positioned HTML at a fixed CSS size.
    Using that page box as the print sheet size avoids splitting one Scribd page
    across multiple PDF pages.
    """
    paper_size = driver.execute_script(
        """
        const candidates = [
            '.outer_page',
            '.newpage',
            '.outer_page_container',
            "[class*='page']"
        ];

        for (const selector of candidates) {
            const element = document.querySelector(selector);
            if (!element) {
                continue;
            }

            const rect = element.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                return {
                    widthInches: rect.width / 96,
                    heightInches: rect.height / 96,
                    selector
                };
            }
        }

        return null;
        """
    )

    if not paper_size:
        return {
            "widthInches": DEFAULT_PAPER_WIDTH_INCHES,
            "heightInches": DEFAULT_PAPER_HEIGHT_INCHES,
            "selector": "default",
        }

    return {
        "widthInches": max(1.0, round(paper_size["widthInches"], 3)),
        "heightInches": max(1.0, round(paper_size["heightInches"], 3)),
        "selector": paper_size["selector"],
    }


def read_pdf_stream_to_file(driver, stream_handle, filename):
    """Read a streamed CDP PDF result and write it to disk in chunks."""
    try:
        with open(filename, "wb") as file_handle:
            while True:
                chunk = driver.execute_cdp_cmd(
                    "IO.read",
                    {
                        "handle": stream_handle,
                        "size": PDF_STREAM_CHUNK_SIZE,
                    },
                )

                data = chunk.get("data", "")
                if not data and chunk.get("eof"):
                    break

                if chunk.get("base64Encoded"):
                    file_handle.write(base64.b64decode(data))
                else:
                    file_handle.write(data.encode("utf-8"))

                if chunk.get("eof"):
                    break
    finally:
        driver.execute_cdp_cmd("IO.close", {"handle": stream_handle})


def save_pdf_directly(
    driver,
    filename,
    timeout_seconds=DEFAULT_CDP_TIMEOUT_SECONDS,
    paper_size=None,
):
    """
    Generate and save a PDF using Chrome DevTools Protocol.

    A longer ChromeDriver timeout plus transferMode=ReturnAsStream makes large,
    image-heavy documents far less likely to fail with a 120 second read timeout.
    """
    configure_command_timeout(driver, timeout_seconds)

    if paper_size is None:
        paper_size = {
            "widthInches": DEFAULT_PAPER_WIDTH_INCHES,
            "heightInches": DEFAULT_PAPER_HEIGHT_INCHES,
        }

    pdf_options = {
        "landscape": False,
        "displayHeaderFooter": False,
        "printBackground": True,
        "scale": 1,
        "paperWidth": paper_size["widthInches"],
        "paperHeight": paper_size["heightInches"],
        "marginTop": 0,
        "marginBottom": 0,
        "marginLeft": 0,
        "marginRight": 0,
        "preferCSSPageSize": False,
    }

    try:
        try:
            result = driver.execute_cdp_cmd(
                "Page.printToPDF",
                {
                    **pdf_options,
                    "transferMode": "ReturnAsStream",
                },
            )

            if result.get("stream"):
                read_pdf_stream_to_file(driver, result["stream"], filename)
            else:
                pdf_data = base64.b64decode(result["data"])
                with open(filename, "wb") as file_handle:
                    file_handle.write(pdf_data)
        except Exception as stream_error:
            print(f"Streamed PDF export unavailable, retrying without stream mode: {stream_error}")
            result = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
            pdf_data = base64.b64decode(result["data"])
            with open(filename, "wb") as file_handle:
                file_handle.write(pdf_data)

        return os.path.abspath(filename)
    except Exception as error:
        print(f"Error saving PDF: {error}")
        return None


def main():
    """Run the downloader interactively."""
    input_url = input("Input link Scribd: ").strip()

    converted_url = convert_scribd_link(input_url)
    pdf_filename = get_filename_from_url(input_url)

    print(f"Link embed: {converted_url}")
    print(f"Output filename: {pdf_filename}")

    if converted_url == "Invalid Scribd URL":
        print("Error: Please provide a valid Scribd document URL")
        print("Example: https://www.scribd.com/document/123456789/Document-Title")
        print("Example: https://www.scribd.com/doc/123456789/Document-Title")
        raise SystemExit(1)

    driver = None
    runtime_profile_dir = None

    try:
        print("\nStarting Chrome browser...")
        options, runtime_profile_dir = build_chrome_options()
        driver = webdriver.Chrome(options=options)

        driver.get(converted_url)
        time.sleep(1)

        hide_cookie_dialogs(driver)
        print("Cookie dialogs hidden.")

        total_pages = scroll_through_pages(driver, DEFAULT_SCROLL_DELAY_SECONDS)
        if total_pages == 0:
            raise RuntimeError("No printable Scribd pages were detected on the embed page.")

        prepare_document_for_print(driver)
        inject_print_styles(driver)
        wait_for_render_stability(driver, DEFAULT_RENDER_SETTLE_TIMEOUT_SECONDS)
        driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": "print"})
        paper_size = detect_document_paper_size(driver)

        driver.execute_script("window.scrollTo(0, 0);")

        print(f"\nSaving PDF as: {pdf_filename}")
        print(
            f'  Page size: {paper_size["widthInches"]:.2f}" x '
            f'{paper_size["heightInches"]:.2f}" '
            f'(from {paper_size["selector"]})'
        )
        print("  Margins: None")
        print("  Headers/Footers: Disabled")
        print(f"  ChromeDriver command timeout: {DEFAULT_CDP_TIMEOUT_SECONDS}s")

        saved_path = save_pdf_directly(driver, pdf_filename, paper_size=paper_size)
        if not saved_path:
            raise RuntimeError("PDF export failed.")

        print(f"PDF saved successfully to: {saved_path}")
    except (RuntimeError, WebDriverException) as error:
        print(f"Download failed: {error}")
        raise SystemExit(1)
    finally:
        if driver is not None:
            driver.quit()
            print("Browser closed.")
        if runtime_profile_dir and os.path.isdir(runtime_profile_dir):
            shutil.rmtree(runtime_profile_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
