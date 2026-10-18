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
import tempfile
import time
from io import BytesIO
from urllib.parse import unquote, urlparse

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options


DEFAULT_CDP_TIMEOUT_SECONDS = int(os.getenv("SCRIBD_CDP_TIMEOUT", "600"))
DEFAULT_RENDER_SETTLE_TIMEOUT_SECONDS = int(
    os.getenv("SCRIBD_RENDER_SETTLE_TIMEOUT", "30")
)
DEFAULT_SCROLL_DELAY_SECONDS = float(os.getenv("SCRIBD_SCROLL_DELAY", "0.15"))
DEFAULT_PAGE_LOAD_CONCURRENCY = max(
    1,
    int(os.getenv("SCRIBD_PAGE_LOAD_CONCURRENCY", "8")),
)
DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS = max(
    10,
    int(os.getenv("SCRIBD_PAGE_LOAD_TIMEOUT", "120")),
)
DEFAULT_EXPORT_BATCH_SIZE = max(
    1,
    int(os.getenv("SCRIBD_EXPORT_BATCH_SIZE", "8")),
)
DEFAULT_PAGE_COUNT_TIMEOUT_SECONDS = max(
    5,
    int(os.getenv("SCRIBD_PAGE_COUNT_TIMEOUT", "60")),
)
DEFAULT_PAGE_COUNT_SETTLE_SECONDS = max(
    0.5,
    float(os.getenv("SCRIBD_PAGE_COUNT_SETTLE", "3")),
)
PDF_STREAM_CHUNK_SIZE = int(os.getenv("SCRIBD_PDF_STREAM_CHUNK_SIZE", str(1024 * 1024)))
HEADLESS_ENABLED = os.getenv("SCRIBD_HEADLESS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_PAPER_WIDTH_INCHES = 7.25
DEFAULT_PAPER_HEIGHT_INCHES = 10.5


def build_chrome_options(runtime_profile_dir):
    """Create Chrome options for reliable headless PDF generation."""
    options = Options()

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
    return options


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


def wait_for_page_numbers(
    driver,
    timeout_seconds=DEFAULT_PAGE_COUNT_TIMEOUT_SECONDS,
    settle_seconds=DEFAULT_PAGE_COUNT_SETTLE_SECONDS,
):
    """
    Return every page number the Scribd viewer knows about, once that list
    has stopped changing for `settle_seconds`.

    window.docManager.pages is the source of truth for the document. Counting
    .outer_page elements one second after load undercounts whenever the
    viewer is still building the document, which silently truncates the PDF.
    """
    deadline = time.monotonic() + timeout_seconds
    last_numbers = None
    last_change = time.monotonic()

    while True:
        numbers = driver.execute_script(
            """
            const manager = window.docManager;
            if (!manager || !manager.pages) {
                return [];
            }
            return Object.keys(manager.pages)
                .filter((key) => manager.pages[key])
                .map(Number)
                .filter((num) => Number.isInteger(num) && num > 0)
                .sort((a, b) => a - b);
            """
        )
        now = time.monotonic()

        if numbers != last_numbers:
            last_numbers = numbers
            last_change = now
        elif numbers and now - last_change >= settle_seconds:
            return numbers

        if now >= deadline:
            return last_numbers or []

        time.sleep(0.25)


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


def load_all_pages(driver):
    """Load Scribd pages directly, without simulating user scrolling."""
    page_count = driver.execute_script(
        """
        return window.docManager && window.docManager.pages
            ? Object.values(window.docManager.pages).filter(Boolean).length
            : 0;
        """
    )
    batch_count = max(
        1,
        (page_count + DEFAULT_PAGE_LOAD_CONCURRENCY - 1)
        // DEFAULT_PAGE_LOAD_CONCURRENCY,
    )
    document_timeout_seconds = (
        DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS * batch_count
    )
    configure_command_timeout(driver, document_timeout_seconds + 10)
    driver.set_script_timeout(document_timeout_seconds + 10)

    result = driver.execute_async_script(
        """
        const concurrency = arguments[0];
        const timeoutMs = arguments[1];
        const documentTimeoutMs = arguments[2];
        const done = arguments[arguments.length - 1];
        const manager = window.docManager;

        if (!manager || !manager.pages) {
            done({supported: false});
            return;
        }

        const pages = Object.values(manager.pages).filter(Boolean);
        const pending = pages.filter((page) => !page.innerPageElem);
        const active = new Map();
        const failed = [];
        const startedAt = Date.now();
        let nextIndex = 0;
        let completed = pages.length - pending.length;
        let finished = false;

        function finish() {
            if (finished) {
                return;
            }

            finished = true;
            clearInterval(timer);

            pages.forEach((page) => {
                if (!page.innerPageElem) {
                    return;
                }

                try {
                    page.display();
                } catch (error) {}

                try {
                    page.turnOnImages();
                } catch (error) {}
            });

            done({
                supported: true,
                total: pages.length,
                loaded: pages.filter((page) => page.innerPageElem).length,
                failed,
                elapsedMs: Date.now() - startedAt
            });
        }

        function launchMore() {
            while (active.size < concurrency && nextIndex < pending.length) {
                const page = pending[nextIndex++];

                try {
                    if (!page.loadHasStarted) {
                        page.load();
                    }

                    active.set(page.pageNum, {
                        page,
                        startedAt: Date.now()
                    });
                } catch (error) {
                    failed.push({
                        pageNum: page.pageNum,
                        reason: String(error)
                    });
                }
            }

            if (completed + failed.length >= pages.length) {
                finish();
            }
        }

        const timer = setInterval(() => {
            for (const [pageNum, state] of active) {
                if (state.page.innerPageElem) {
                    active.delete(pageNum);
                    completed += 1;
                    continue;
                }

                if (Date.now() - state.startedAt >= timeoutMs) {
                    active.delete(pageNum);
                    failed.push({
                        pageNum,
                        reason: 'page load timed out'
                    });
                }
            }

            if (Date.now() - startedAt >= documentTimeoutMs) {
                for (const [pageNum] of active) {
                    failed.push({
                        pageNum,
                        reason: 'document load timed out'
                    });
                }
                active.clear();
                finish();
                return;
            }

            launchMore();
        }, 50);

        launchMore();
        """,
        DEFAULT_PAGE_LOAD_CONCURRENCY,
        DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS * 1000,
        document_timeout_seconds * 1000,
    )

    if not result.get("supported"):
        print("Direct page loader unavailable; using scrolling fallback.")
        return scroll_through_pages(driver, DEFAULT_SCROLL_DELAY_SECONDS)

    total_pages = result["total"]
    loaded_pages = result["loaded"]
    elapsed_seconds = result["elapsedMs"] / 1000

    print(
        f"Loaded {loaded_pages}/{total_pages} pages directly "
        f"in {elapsed_seconds:.2f}s "
        f"(concurrency: {DEFAULT_PAGE_LOAD_CONCURRENCY})."
    )

    if result["failed"]:
        failed_pages = ", ".join(
            str(item["pageNum"])
            for item in result["failed"]
        )
        raise RuntimeError(
            "Failed to load Scribd page(s): "
            f"{failed_pages}"
        )

    return loaded_pages


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
                return JSON.stringify({
                    pageCount: pages.length,
                    heights,
                    pendingImages
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
                const isBusy = parsed.pendingImages > 0;

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


def load_page_batch(driver, page_numbers):
    """Load one bounded batch of page DOM and image assets."""
    configure_command_timeout(
        driver,
        DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS + 10,
    )
    driver.set_script_timeout(DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS + 10)

    result = driver.execute_async_script(
        """
        const pageNumbers = arguments[0];
        const timeoutMs = arguments[1];
        const done = arguments[arguments.length - 1];
        const manager = window.docManager;

        if (!manager || !manager.pages) {
            done({supported: false});
            return;
        }

        const states = pageNumbers.map((pageNum) => ({
            pageNum,
            page: manager.pages[pageNum],
            error: null
        }));
        const startedAt = Date.now();

        for (const state of states) {
            if (!state.page) {
                state.error = 'page object missing';
                continue;
            }

            try {
                if (!state.page.innerPageElem && !state.page.loadHasStarted) {
                    state.page.load();
                }
            } catch (error) {
                state.error = String(error);
            }
        }

        const timer = setInterval(() => {
            let ready = 0;

            for (const state of states) {
                if (state.error) {
                    ready += 1;
                    continue;
                }

                const page = state.page;
                if (!page.innerPageElem) {
                    continue;
                }

                try {
                    page.display();
                    if (!page._imagesTurnedOn) {
                        page.turnOnImages();
                    }
                } catch (error) {
                    state.error = String(error);
                    ready += 1;
                    continue;
                }

                const images = Array.from(
                    page.innerPageElem.querySelectorAll('img')
                );
                const pending = images.filter((image) => !image.complete);

                if (pending.length === 0) {
                    ready += 1;
                }
            }

            if (ready === states.length) {
                clearInterval(timer);
                done({
                    supported: true,
                    failed: states
                        .filter((state) => state.error)
                        .map((state) => ({
                            pageNum: state.pageNum,
                            reason: state.error
                        }))
                });
                return;
            }

            if (Date.now() - startedAt >= timeoutMs) {
                clearInterval(timer);
                done({
                    supported: true,
                    failed: states
                        .filter((state) => (
                            state.error ||
                            !state.page ||
                            !state.page.innerPageElem ||
                            Array.from(
                                state.page.innerPageElem.querySelectorAll('img')
                            ).some((image) => !image.complete)
                        ))
                        .map((state) => ({
                            pageNum: state.pageNum,
                            reason: state.error || 'page or image load timed out'
                        }))
                });
            }
        }, 50);
        """,
        list(page_numbers),
        DEFAULT_PAGE_LOAD_TIMEOUT_SECONDS * 1000,
    )

    if not result.get("supported"):
        raise RuntimeError("Scribd direct page loader is unavailable.")

    if result["failed"]:
        details = ", ".join(
            f"{item['pageNum']} ({item['reason']})"
            for item in result["failed"]
        )
        raise RuntimeError(f"Failed to load Scribd page(s): {details}")


def release_page_batch(driver, page_numbers):
    """Release printed page DOM and image resources from Chrome."""
    driver.execute_script(
        """
        const manager = window.docManager;
        if (!manager || !manager.pages) {
            return;
        }

        for (const pageNum of arguments[0]) {
            const page = manager.pages[pageNum];
            if (!page) {
                continue;
            }

            try {
                page.remove();
            } catch (error) {
                const container = document.getElementById(`outer_page_${pageNum}`);
                if (container) {
                    const inner = container.querySelector('.newpage');
                    if (inner) {
                        inner.remove();
                    }
                }
            }
        }
        """,
        list(page_numbers),
    )

    try:
        driver.execute_cdp_cmd("HeapProfiler.collectGarbage", {})
    except WebDriverException:
        pass


def save_pdf_pages_individually(
    driver,
    filename,
    page_numbers,
    timeout_seconds=DEFAULT_CDP_TIMEOUT_SECONDS,
):
    from pypdf import PdfReader, PdfWriter

    configure_command_timeout(
        driver,
        timeout_seconds,
    )

    # Pages are addressed by their Scribd page number (#outer_page_N), never
    # by their position in a live .outer_page list: release_page_batch() can
    # take finished pages out of the DOM, which shifts every position after
    # them and made the old loop print the wrong (unloaded) pages.
    page_numbers = list(page_numbers)
    page_count = len(page_numbers)

    if page_count <= 0:
        raise RuntimeError(
            "No Scribd pages to export."
        )

    print(
        f"Exporting {page_count} "
        "document pages in bounded batches "
        f"of {DEFAULT_EXPORT_BATCH_SIZE}..."
    )

    spool = tempfile.TemporaryDirectory(
        prefix="scribd-pdf-pages-"
    )
    page_files = []

    try:
        for index in range(page_count):
            if index % DEFAULT_EXPORT_BATCH_SIZE == 0:
                batch_end = min(
                    page_count,
                    index + DEFAULT_EXPORT_BATCH_SIZE,
                )
                batch_page_numbers = page_numbers[
                    index:batch_end
                ]
                print(
                    f"  Loading page batch "
                    f"{index + 1}-{batch_end}/{page_count}..."
                )
                load_page_batch(
                    driver,
                    batch_page_numbers,
                )

            page_num = page_numbers[index]

            page_info = driver.execute_script(
                """
                const pageNum = arguments[0];

                const pages = Array.from(
                    document.querySelectorAll(
                        '.outer_page'
                    )
                );

                const target = document.getElementById(
                    `outer_page_${pageNum}`
                );

                if (!target) {
                    return null;
                }

                /*
                 * Remove previous isolated-print style.
                 */
                const oldStyle = document.getElementById(
                    'isolated-page-print-style'
                );

                if (oldStyle) {
                    oldStyle.remove();
                }

                /*
                 * Restore all pages before measuring.
                 */
                pages.forEach((page) => {
                    page.style.removeProperty('display');
                    page.style.removeProperty('visibility');
                    page.style.removeProperty('position');
                    page.style.removeProperty('top');
                    page.style.removeProperty('left');
                    page.style.removeProperty('right');
                    page.style.removeProperty('bottom');
                    page.style.removeProperty('margin');
                    page.style.removeProperty('break-after');
                    page.style.removeProperty('page-break-after');
                    page.style.removeProperty('break-before');
                    page.style.removeProperty('page-break-before');
                });

                const rect = target.getBoundingClientRect();

                const width = Math.ceil(rect.width);
                const height = Math.ceil(rect.height);

                /*
                 * Mark the target instead of relying on nth-child.
                 */
                pages.forEach((page) => {
                    page.removeAttribute(
                        'data-export-target'
                    );
                });

                target.setAttribute(
                    'data-export-target',
                    'true'
                );

                /*
                 * Scribd centers pages inside a flex .document_scroller.
                 * Once the target is position:absolute its wrappers
                 * collapse to zero width and get centered, so the page
                 * started at x = width / 2 and its right half was cut
                 * off the sheet. Mark every wrapper so the print CSS can
                 * flatten it and the page lands at the sheet's corner.
                 */
                document.querySelectorAll(
                    '[data-export-ancestor]'
                ).forEach((node) => {
                    node.removeAttribute('data-export-ancestor');
                });

                for (
                    let node = target.parentElement;
                    node && node !== document.body;
                    node = node.parentElement
                ) {
                    node.setAttribute(
                        'data-export-ancestor',
                        'true'
                    );
                }

                const style = document.createElement(
                    'style'
                );

                style.id = 'isolated-page-print-style';

                style.textContent = `
                    @page {
                        size: ${width}px ${height}px;
                        margin: 0;
                    }

                    @media print {
                        html,
                        body {
                            width: ${width}px !important;
                            height: ${height}px !important;
                            min-width: ${width}px !important;
                            min-height: ${height}px !important;
                            max-width: ${width}px !important;
                            max-height: ${height}px !important;

                            margin: 0 !important;
                            padding: 0 !important;

                            overflow: hidden !important;

                            -webkit-print-color-adjust:
                                exact !important;

                            print-color-adjust:
                                exact !important;
                        }

                        .outer_page {
                            display: none !important;
                        }

                        [data-export-ancestor="true"] {
                            position: static !important;
                            display: block !important;
                            transform: none !important;
                            margin: 0 !important;
                            padding: 0 !important;
                            left: auto !important;
                            top: auto !important;
                        }

                        .outer_page[
                            data-export-target="true"
                        ] {
                            display: block !important;
                            visibility: visible !important;

                            box-sizing: border-box !important;

                            position: absolute !important;

                            top: 0 !important;
                            left: 0 !important;
                            right: auto !important;
                            bottom: auto !important;

                            width: ${width}px !important;
                            height: ${height}px !important;

                            min-width: 0 !important;
                            min-height: 0 !important;

                            max-width: none !important;
                            max-height: none !important;

                            margin: 0 !important;
                            padding: 0 !important;

                            transform: none !important;

                            break-before: auto !important;
                            break-after: auto !important;
                            break-inside: auto !important;

                            page-break-before:
                                auto !important;

                            page-break-after:
                                auto !important;

                            page-break-inside:
                                auto !important;

                            overflow: hidden !important;
                        }
                    }
                `;

                document.head.appendChild(style);

                return {
                    width,
                    height
                };
                """,
                page_num,
            )

            # Fail loudly instead of silently skipping: a skipped page is
            # exactly how the PDF ended up incomplete without any error.
            if not page_info:
                raise RuntimeError(
                    f"Scribd page {page_num} has no "
                    f"#outer_page_{page_num} element."
                )

            width_px = int(page_info["width"])
            height_px = int(page_info["height"])

            if width_px <= 0 or height_px <= 0:
                raise RuntimeError(
                    f"Scribd page {page_num} has invalid "
                    f"geometry {width_px}x{height_px}."
                )

            width_inches = width_px / 96.0
            height_inches = height_px / 96.0

            print(
                f"  Page {index + 1}/{page_count} "
                f"{width_px}x{height_px}px "
                f"-> "
                f'{width_inches:.3f}"'
                f'x{height_inches:.3f}"'
            )

            driver.execute_cdp_cmd(
                "Emulation.setEmulatedMedia",
                {
                    "media": "print",
                },
            )

            # Check where the page actually lands in print layout. A page
            # that does not start at the sheet's corner, or does not fill
            # it, would come out cropped.
            placed = driver.execute_script(
                """
                const rect = document.getElementById(
                    `outer_page_${arguments[0]}`
                ).getBoundingClientRect();
                return [rect.left, rect.top, rect.width, rect.height];
                """,
                page_num,
            )
            left, top, placed_w, placed_h = placed
            if (
                abs(left) > 1
                or abs(top) > 1
                or abs(placed_w - width_px) > 1
                or abs(placed_h - height_px) > 1
            ):
                raise RuntimeError(
                    f"Scribd page {page_num} would print at "
                    f"({left:.0f}, {top:.0f}) with size "
                    f"{placed_w:.0f}x{placed_h:.0f} on a "
                    f"{width_px}x{height_px} sheet; refusing "
                    "to write a cropped page."
                )

            result = driver.execute_cdp_cmd(
                "Page.printToPDF",
                {
                    "landscape": False,
                    "displayHeaderFooter": False,
                    "printBackground": True,

                    "scale": 1,

                    "paperWidth": width_inches,
                    "paperHeight": height_inches,

                    "marginTop": 0,
                    "marginBottom": 0,
                    "marginLeft": 0,
                    "marginRight": 0,

           
                    "preferCSSPageSize": True,

                
                    "pageRanges": "1",

                    "transferMode": "ReturnAsBase64",
                },
            )

            pdf_bytes = base64.b64decode(
                result["data"]
            )

            page_reader = PdfReader(BytesIO(pdf_bytes))
            if len(page_reader.pages) != 1:
                raise RuntimeError(
                    f"Document page "
                    f"{index + 1} produced "
                    f"{len(page_reader.pages)} "
                    "PDF sheets; expected exactly 1."
                )

            page_path = os.path.join(
                spool.name,
                f"page-{index + 1:08d}.pdf",
            )
            with open(page_path, "wb") as page_handle:
                page_handle.write(pdf_bytes)
            page_files.append(page_path)

            print(
                f"    OK: exactly 1 PDF sheet"
            )

            is_batch_end = (
                (index + 1) % DEFAULT_EXPORT_BATCH_SIZE == 0
                or index + 1 == page_count
            )
            if is_batch_end:
                release_page_batch(
                    driver,
                    batch_page_numbers,
                )

        if len(page_files) != page_count:
            raise RuntimeError(
                f"Exported {len(page_files)} of "
                f"{page_count} pages; refusing to "
                "write an incomplete PDF."
            )

        print(
            f"Merging {len(page_files)} "
            "disk-spooled PDF pages..."
        )
        writer = PdfWriter()
        try:
            for page_path in page_files:
                writer.append(page_path)
            with open(filename, "wb") as output_handle:
                writer.write(output_handle)
        finally:
            writer.close()

    finally:
        spool.cleanup()

    return os.path.abspath(filename)

def main():
    """Run the exporter interactively."""
    input_url = input("Input link Scribd: ").strip()

    converted_url = convert_scribd_link(input_url)
    pdf_filename = get_filename_from_url(input_url)

    print(f"Link embed: {converted_url}")
    print(f"Output filename: {pdf_filename}")

    if converted_url == "Invalid Scribd URL":
        print("Error: Please provide a valid Scribd document URL")
        print(
            "Example: "
            "https://www.scribd.com/document/"
            "123456789/Document-Title"
        )
        print(
            "Example: "
            "https://www.scribd.com/doc/"
            "123456789/Document-Title"
        )
        raise SystemExit(1)

    with tempfile.TemporaryDirectory(
        prefix="scribd-chrome-profile-"
    ) as runtime_profile_dir:
        driver = None

        try:
            print("\nStarting Chrome browser...")

            options = build_chrome_options(
                runtime_profile_dir
            )

            driver = webdriver.Chrome(
                options=options
            )

            driver.get(converted_url)
            time.sleep(1)

            hide_cookie_dialogs(driver)
            print("Cookie dialogs hidden.")

            page_numbers = wait_for_page_numbers(driver)

            if not page_numbers:
                raise RuntimeError(
                    "No printable document pages "
                    "were detected (window.docManager "
                    "has no pages)."
                )

            print(
                f"Scribd viewer reports "
                f"{len(page_numbers)} pages."
            )

            prepare_document_for_print(driver)

            inject_print_styles(driver)

            print(
                f"\nSaving PDF as: {pdf_filename}"
            )

            print(
                "  Export mode: "
                "Individual document pages"
            )

            print("  Margins: None")

            print(
                "  Headers/Footers: Disabled"
            )

            print(
                "  ChromeDriver command timeout: "
                f"{DEFAULT_CDP_TIMEOUT_SECONDS}s"
            )

            driver.execute_script(
                "window.scrollTo(0, 0)"
            )

            saved_path = (
                save_pdf_pages_individually(
                    driver,
                    pdf_filename,
                    page_numbers,
                )
            )

            if not saved_path:
                raise RuntimeError(
                    "PDF export failed."
                )

            print(
                "PDF saved successfully to: "
                f"{saved_path}"
            )

        except (
            RuntimeError,
            WebDriverException,
        ) as error:
            print(
                f"Export failed: {error}"
            )

            raise SystemExit(1)

        finally:
            if driver is not None:
                driver.quit()
                print("Browser closed.")


if __name__ == "__main__":
    main()
    
