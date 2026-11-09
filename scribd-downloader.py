"""
Scribd Document Downloader
==========================

A Selenium-based utility that loads a Scribd embed and saves it as a PDF,
plain text (TXT) and/or an editable Word document (DOCX).

Key behaviors:
1. Converts a Scribd document URL (any scribd.com subdomain, optional
   secret_password) to the embed/content URL.
2. Opens the document in Chrome and waits for the viewer's page list.
3. Scrolls through every page to trigger lazy loading.
4. Removes UI overlays without stripping layout classes needed for rendering.
5. Waits for fonts, images, and page geometry to settle.
6. Saves the PDF through Chrome DevTools Protocol with a larger timeout and
   stream-based PDF transfer for large documents.
"""

import argparse
import base64
import os
import re
import sys
import tempfile
import time
import urllib.request
from io import BytesIO
from urllib.parse import parse_qs, quote, unquote, urlparse

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
PDF_STREAM_CHUNK_SIZE = int(os.getenv("SCRIBD_PDF_STREAM_CHUNK_SIZE", str(1024 * 1024)))
HEADLESS_ENABLED = os.getenv("SCRIBD_HEADLESS", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_PAPER_WIDTH_INCHES = 7.25
DEFAULT_PAPER_HEIGHT_INCHES = 10.5
DEFAULT_DOCUMENT_READY_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("SCRIBD_DOCUMENT_READY_TIMEOUT", "30")),
)
SUPPORTED_FORMATS = ("pdf", "txt", "docx")
INVALID_URL = "Invalid Scribd URL"
# Characters that are not allowed in Windows filenames (plus control chars).
UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


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


def is_scribd_host(hostname):
    """Return True only for scribd.com and its real subdomains."""
    hostname = (hostname or "").lower().rstrip(".")
    return hostname == "scribd.com" or hostname.endswith(".scribd.com")


def parse_scribd_url(url):
    """
    Parse a Scribd document URL.

    Accepts any scribd.com subdomain (www, de, es, fr, ...), http or https,
    a missing scheme, a missing title slug, and the legacy /doc/ path.
    Lookalike hosts such as notscribd.com are rejected.

    Returns:
        A dict with "doc_id", "slug", "secret_password" and "embed_url",
        or None if the URL is not a Scribd document link.
    """
    url = (url or "").strip()
    if not url:
        return None

    if "://" not in url:
        url = f"https://{url}"

    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"} or not is_scribd_host(parsed.hostname):
        return None

    match = re.match(r"^/(?:document|doc|embeds)/(\d+)(?:/([^/?#]*))?", parsed.path)
    if not match:
        return None

    doc_id = match.group(1)
    slug = unquote(match.group(2) or "")
    if slug == "content":
        # /embeds/<id>/content is the embed URL itself, not a title.
        slug = ""

    secret_values = parse_qs(parsed.query).get("secret_password")
    secret_password = secret_values[0] if secret_values else None

    embed_url = f"https://www.scribd.com/embeds/{doc_id}/content"
    if secret_password:
        embed_url += f"?secret_password={quote(secret_password, safe='')}"

    return {
        "doc_id": doc_id,
        "slug": slug,
        "secret_password": secret_password,
        "embed_url": embed_url,
    }


def convert_scribd_link(url):
    """
    Convert a Scribd document URL to the embed/content URL.

    Args:
        url: Scribd URL such as
            https://www.scribd.com/document/123456789/Document-Title,
            https://de.scribd.com/document/123456789/Document-Title
            or https://www.scribd.com/doc/123456789/Document-Title

    Returns:
        The embeddable content URL, or "Invalid Scribd URL" if no document id
        can be extracted.
    """
    info = parse_scribd_url(url)
    if not info:
        return INVALID_URL

    return info["embed_url"]


def redact_url(url):
    """Hide secret query values (e.g. secret_password) before printing a URL."""
    return re.sub(r"(secret_password=)[^&#]*", r"\1REDACTED", url or "")


def sanitize_filename(name, fallback="scribd_document"):
    """Make a string safe to use as a filename on Windows, macOS and Linux."""
    name = UNSAFE_FILENAME_CHARS.sub("_", name or "").strip().strip(".")
    return name or fallback


def get_base_name_from_url(url):
    """Build an output base name (no extension) from a Scribd URL."""
    info = parse_scribd_url(url)
    if info:
        return sanitize_filename(info["slug"] or info["doc_id"])

    parsed = urlparse(url or "")
    path = parsed.path.rstrip("/")
    last_segment = path.split("/")[-1] if path else ""
    return sanitize_filename(unquote(last_segment))


def get_filename_from_url(url):
    """
    Build an output filename from the document title in the URL.

    Args:
        url: Scribd document URL.

    Returns:
        Filename ending in ".pdf".
    """
    return f"{get_base_name_from_url(url)}.pdf"


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


def count_document_pages(driver):
    """Return how many Scribd pages the viewer currently knows about."""
    return driver.execute_script(
        """
        const outer = document.querySelectorAll('.outer_page').length;
        const manager = window.docManager;
        const managed = manager && manager.pages
            ? Object.values(manager.pages).filter(Boolean).length
            : 0;
        return Math.max(outer, managed);
        """
    ) or 0


def wait_for_document_ready(
    driver,
    timeout_seconds=DEFAULT_DOCUMENT_READY_TIMEOUT_SECONDS,
    poll_seconds=0.25,
):
    """
    Wait until the Scribd viewer has created its page list.

    The old code slept for a fixed second, which is too short on slow servers
    and VPS machines (issue #7). This polls until the page count is non-zero
    and unchanged across two checks, or until the timeout.

    Returns:
        The page count, or 0 if no pages appeared in time.
    """
    deadline = time.monotonic() + timeout_seconds
    last_count = -1
    stable_checks = 0

    while True:
        try:
            count = count_document_pages(driver)
        except WebDriverException:
            count = 0

        if count > 0 and count == last_count:
            stable_checks += 1
            if stable_checks >= 2:
                return count
        else:
            stable_checks = 0

        last_count = count

        if time.monotonic() >= deadline:
            return count

        time.sleep(poll_seconds)


def classify_load_failure(title, body_text, current_url):
    """
    Guess why a Scribd embed showed no pages, from what the page displays.

    Returns:
        A short human-readable reason.
    """
    title = (title or "").lower()
    text = (body_text or "").lower()
    current_url = (current_url or "").lower()
    combined = f"{title}\n{text}"

    if any(
        marker in combined
        for marker in (
            "just a moment",
            "checking your browser",
            "verify you are human",
            "attention required",
            "cf-chl",
            "enable javascript and cookies",
        )
    ):
        return (
            "Scribd showed a bot check (Cloudflare). This usually happens on "
            "VPS or data-centre IP addresses. Try from a home connection, a "
            "residential proxy, or visible mode with SCRIBD_HEADLESS=0."
        )

    if any(marker in combined for marker in ("access denied", "403 forbidden", "blocked")):
        return (
            "Scribd denied access from this network. VPS and data-centre IP "
            "addresses are often blocked."
        )

    if any(
        marker in combined
        for marker in (
            "document not found",
            "can't display this document",
            "page not found",
            "404",
            "no longer available",
            "has been removed",
        )
    ):
        return "The document was not found. It may have been deleted or made private."

    if "secret_password" in current_url:
        return "The secret_password in this shared link may be wrong or expired."

    if "private" in combined:
        return (
            "The document may be private. Use the full shared link, including "
            "its secret_password parameter."
        )

    if "/login" in current_url or "log in" in combined or "sign in" in combined:
        return "Scribd redirected to a login page. This document may require an account."

    return (
        "The Scribd viewer did not load any pages. The network may be slow, "
        "or Scribd may be blocking this IP. Try increasing "
        "SCRIBD_DOCUMENT_READY_TIMEOUT or running with SCRIBD_HEADLESS=0."
    )


def diagnose_load_failure(driver, debug_basename=None):
    """
    Build a detailed error message when no document pages were detected.

    Optionally saves a screenshot and the page HTML next to the output so the
    user can see what Scribd actually served.
    """
    try:
        title = driver.title
        current_url = driver.current_url
        body_text = driver.execute_script(
            "return document.body ? document.body.innerText.slice(0, 4000) : '';"
        )
    except WebDriverException:
        title, current_url, body_text = "", "", ""

    reason = classify_load_failure(title, body_text, current_url)
    lines = [
        "No printable document pages were detected.",
        f"  Reason: {reason}",
        f"  Page title: {title!r}",
        f"  Page URL: {redact_url(current_url)}",
    ]

    if debug_basename:
        try:
            screenshot_path = f"{debug_basename}.png"
            html_path = f"{debug_basename}.html"
            driver.save_screenshot(screenshot_path)
            with open(html_path, "w", encoding="utf-8") as handle:
                handle.write(driver.page_source)
            lines.append(f"  Debug screenshot: {os.path.abspath(screenshot_path)}")
            lines.append(f"  Debug HTML: {os.path.abspath(html_path)}")
        except (OSError, WebDriverException) as error:
            lines.append(f"  Could not save debug files: {error}")

    return "\n".join(lines)


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

def extract_page_content(driver, page_numbers):
    """
    Read the text runs and images of loaded Scribd pages from the live HTML.

    Scribd renders each page as HTML: absolutely positioned text spans that
    carry the uploaded document's fonts, sizes and colours, plus <img> tags
    for figures. Every position and size is returned in the same CSS pixel
    space as the rendered .outer_page box (the one the PDF export prints), so
    1px == 0.75pt.
    """
    # Print emulation hides every page except the one being printed, which
    # would make all measurements zero. Measure in normal screen media.
    try:
        driver.execute_cdp_cmd("Emulation.setEmulatedMedia", {"media": ""})
    except WebDriverException:
        pass

    return driver.execute_script(
        """
        const pageNumbers = arguments[0];
        const scaleCache = new Map();

        function ownScale(element) {
            const transform = getComputedStyle(element).transform;
            if (!transform || transform === 'none') {
                return 1;
            }
            const match = transform.match(/matrix(3d)?\\(([^)]+)\\)/);
            if (!match) {
                return 1;
            }
            const values = match[2].split(',').map(parseFloat);
            return Math.hypot(values[0], values[1]) || 1;
        }

        function cumulativeScale(element, root) {
            if (!element || element === root.parentElement) {
                return 1;
            }
            if (scaleCache.has(element)) {
                return scaleCache.get(element);
            }
            const scale = ownScale(element) * cumulativeScale(element.parentElement, root);
            scaleCache.set(element, scale);
            return scale;
        }

        function fontName(fontFamily) {
            const names = fontFamily
                .split(',')
                .map((name) => name.trim().replace(/^["']|["']$/g, ''))
                .filter(Boolean);
            // Scribd names embedded fonts ff0, ff1, ...; the next entry is
            // the original font's closest system equivalent.
            const real = names.find((name) => !/^ff[0-9a-z]+$/i.test(name));
            return real || names[0] || '';
        }

        const measureContext = document.createElement('canvas').getContext('2d');

        // Scribd stacks text and image layers (e.g. text z=2, images z=3,
        // text z=4). Text in a lower layer can be hidden under an image.
        function layerZ(element) {
            const layer = element.closest('.text_layer, .image_layer');
            if (!layer) {
                return 0;
            }
            return parseInt(getComputedStyle(layer).zIndex, 10) || 0;
        }

        return pageNumbers.map((pageNum) => {
            const outer = document.getElementById(`outer_page_${pageNum}`);
            if (!outer) {
                return {pageNum, width: 0, height: 0, runs: [], images: []};
            }

            const base = outer.getBoundingClientRect();
            const textRoots = outer.querySelectorAll('.text_layer');
            const roots = textRoots.length ? Array.from(textRoots) : [outer];
            const runs = [];

            for (const root of roots) {
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const text = node.textContent;
                    if (!text) {
                        continue;
                    }

                    const parent = node.parentElement;
                    const style = getComputedStyle(parent);
                    if (style.visibility === 'hidden' || style.display === 'none') {
                        continue;
                    }

                    const range = document.createRange();
                    range.selectNodeContents(node);
                    const rect = range.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) {
                        continue;
                    }

                    const size = parseFloat(style.fontSize) * cumulativeScale(parent, outer);
                    const weight = parseInt(style.fontWeight, 10) || 400;
                    const family = fontName(style.fontFamily);

                    // Width of the visible glyphs (trailing spaces carry
                    // Scribd's huge word-spacing, so leave them out) and the
                    // width the same text takes in the substitute font Word
                    // will use. Their ratio lets the DOCX keep line widths.
                    let visibleWidth = 0;
                    let fallbackWidth = 0;
                    let visibleLeft = 0;
                    measureContext.font = `${style.fontStyle} ${weight} ${size}px "${family}"`;
                    const spaceWidth = measureContext.measureText(' ').width;
                    const trimmed = text.replace(/\\s+$/, '');
                    const lead = trimmed.length - trimmed.replace(/^\\s+/, '').length;
                    if (trimmed.length > lead) {
                        const visibleRange = document.createRange();
                        visibleRange.setStart(node, lead);
                        visibleRange.setEnd(node, trimmed.length);
                        const visibleRect = visibleRange.getBoundingClientRect();
                        visibleWidth = visibleRect.width;
                        visibleLeft = visibleRect.left - base.left;
                        fallbackWidth = measureContext.measureText(
                            trimmed.slice(lead).replace(/\\u00a0/g, ' ')
                        ).width;
                    }

                    runs.push({
                        text,
                        vx: visibleLeft,
                        vw: visibleWidth,
                        fw: fallbackWidth,
                        sw: spaceWidth,
                        x: rect.left - base.left,
                        y: rect.top - base.top,
                        w: rect.width,
                        size,
                        color: style.color,
                        font: family,
                        bold: weight >= 600 || /bold|black|heavy/i.test(family),
                        italic: style.fontStyle !== 'normal' || /italic|oblique/i.test(family),
                        spacing: (parseFloat(style.letterSpacing) || 0) * cumulativeScale(parent, outer),
                        z: layerZ(parent)
                    });
                }
            }

            // Scribd reuses one image as a sprite and shows part of it with
            // CSS clip: rect(top, right, bottom, left). Report the visible
            // area plus crop fractions.
            function clipFractions(image) {
                const clip = getComputedStyle(image).clip;
                const width = image.offsetWidth;
                const height = image.offsetHeight;
                const crop = {top: 0, right: 1, bottom: 1, left: 0};
                const match = clip && clip.match(/rect\\(([^)]+)\\)/);
                if (!match || !width || !height) {
                    return crop;
                }
                const parts = match[1].split(/[,\\s]+/).filter(Boolean);
                if (parts.length !== 4) {
                    return crop;
                }
                const value = (part, fallback, total) =>
                    part === 'auto' ? fallback : Math.min(1, Math.max(0, parseFloat(part) / total));
                crop.top = value(parts[0], 0, height);
                crop.right = value(parts[1], 1, width);
                crop.bottom = value(parts[2], 1, height);
                crop.left = value(parts[3], 0, width);
                return crop;
            }

            const images = Array.from(outer.querySelectorAll('img'))
                .map((image) => {
                    const rect = image.getBoundingClientRect();
                    const crop = clipFractions(image);
                    // Also cut away anything outside the page box.
                    const left = Math.max(rect.left + crop.left * rect.width, base.left);
                    const right = Math.min(rect.left + crop.right * rect.width, base.right);
                    const top = Math.max(rect.top + crop.top * rect.height, base.top);
                    const bottom = Math.min(rect.top + crop.bottom * rect.height, base.bottom);
                    return {
                        src: image.currentSrc || image.src || '',
                        x: left - base.left,
                        y: top - base.top,
                        w: right - left,
                        h: bottom - top,
                        z: layerZ(image),
                        crop: rect.width && rect.height ? {
                            left: (left - rect.left) / rect.width,
                            top: (top - rect.top) / rect.height,
                            right: (rect.right - right) / rect.width,
                            bottom: (rect.bottom - bottom) / rect.height
                        } : null
                    };
                })
                .filter((image) => image.src && image.w >= 1 && image.h >= 1 && image.w * image.h >= 16);

            return {
                pageNum,
                width: base.width,
                height: base.height,
                runs,
                images
            };
        });
        """,
        list(page_numbers),
    )


def css_color_to_hex(color):
    """Convert 'rgb(1, 2, 3)' / 'rgba(...)' / '#abc' to 'RRGGBB' (or None)."""
    color = (color or "").strip().lower()
    match = re.match(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)", color)
    if match:
        return "".join(f"{min(255, int(part)):02X}" for part in match.groups())

    match = re.match(r"#([0-9a-f]{3}|[0-9a-f]{6})$", color)
    if match:
        value = match.group(1)
        if len(value) == 3:
            value = "".join(char * 2 for char in value)
        return value.upper()

    return None


NO_SPACE_BEFORE = set(".,;:!?)]}%’”'")
NO_SPACE_AFTER = set("([{‘“$")
LIST_MARKER = re.compile(
    r"^\s*(?:[•●▪■‣⁃◦·\-–—*]"
    r"|\(?(?:\d{1,3}|[a-zA-Z]|[ivxlcdm]{1,6})[.)])\s"
)


def starts_with_list_marker(text):
    """True for lines like '• item', '- item', '1. item', 'a) item', '(iv) item'."""
    return bool(LIST_MARKER.match(text or ""))


def _run_style(run):
    """(font, size px, colour hex, bold, italic, letter spacing px)."""
    size = float(run.get("size") or 0)
    spacing = float(run.get("spacing") or 0)
    # Scribd adds tiny letter-spacing tweaks for kerning, and spacing on
    # one-glyph spans when it positions characters one by one. Only keep
    # real letter-spaced text (e.g. spaced-out headings).
    if abs(spacing) < 0.04 * size or len((run.get("text") or "").strip()) < 4:
        spacing = 0.0
    return (
        run.get("font") or "",
        round(size, 1),
        css_color_to_hex(run.get("color")),
        bool(run.get("bold")),
        bool(run.get("italic")),
        round(spacing, 1),
    )


def build_lines(runs):
    """
    Group positioned text runs into visual lines, left to right.

    Returns a list of lines: {"x", "x_end", "y", "size", "segments"} where
    segments are consecutive same-style pieces of text.
    """
    cleaned = []
    for run in runs or []:
        text = (run.get("text") or "").replace(" ", " ")
        if not text:
            continue
        stripped = text.strip()
        if len(stripped) == 1 and "" <= stripped <= "":
            # Embedded symbol fonts map bullets (and other dingbats) to the
            # Unicode Private Use Area, which no normal font can show.
            text = text.replace(stripped, "•")
        size = float(run.get("size") or 0) or 1.0
        cleaned.append({**run, "text": text, "size": size})

    cleaned.sort(key=lambda run: (run["y"], run["x"]))

    grouped = []
    for run in cleaned:
        if grouped:
            anchor = grouped[-1]["anchor"]
            # Compare with the previous visible run so small glyphs such as
            # bullets (smaller font, lower top) stay on their text's line.
            tolerance = max(1.5, 0.45 * max(run["size"], anchor["size"]))
            if abs(run["y"] - anchor["y"]) <= tolerance:
                grouped[-1]["runs"].append(run)
                if run["text"].strip():
                    grouped[-1]["anchor"] = run
                continue
        grouped.append({"y": run["y"], "anchor": run, "runs": [run]})

    lines = []
    for group in grouped:
        line_runs = sorted(group["runs"], key=lambda run: run["x"])
        segments = []
        previous = None
        previous_visible = None
        tab_stops = []

        for run in line_runs:
            text = run["text"]
            if previous is not None and segments:
                gap = run["x"] - (previous["x"] + float(previous.get("w") or 0))
                size = min(run["size"], previous["size"])
                column_gap = 0.0
                column_size = size
                if previous_visible is not None:
                    column_gap = run["x"] - (
                        previous_visible["x"] + estimated_width(previous_visible)
                    )
                    # Use the larger font: a tiny bullet glyph must not turn
                    # its normal indent into a "column".
                    column_size = max(run["size"], previous_visible["size"])
                before = segments[-1]["text"]
                if column_gap > 2.5 * column_size and text.strip():
                    # Column / tab-stop gap, e.g. "Company      City, State".
                    for segment in reversed(segments):
                        segment["text"] = segment["text"].rstrip()
                        if segment["text"]:
                            break
                    text = "\t" + text.lstrip()
                    tab_stops.append(run["x"])
                elif before and (
                    gap > 0.2 * size
                    and not text.startswith(" ")
                    and not before.endswith(" ")
                    and text[0] not in NO_SPACE_BEFORE
                    and before[-1] not in NO_SPACE_AFTER
                ):
                    text = " " + text

            style = _run_style(run)
            if segments and segments[-1]["style"] == style:
                segments[-1]["text"] += text
            else:
                segments.append({"text": text, "style": style})
            previous = run
            if run["text"].strip():
                previous_visible = run

        if segments:
            segments[0]["text"] = segments[0]["text"].lstrip()
            segments[-1]["text"] = segments[-1]["text"].rstrip()
        segments = [segment for segment in segments if segment["text"]]
        if not segments or not "".join(s["text"] for s in segments).strip():
            continue

        visible = [run for run in line_runs if run["text"].strip()] or line_runs
        width_original, width_word = line_widths(visible, segments, bool(tab_stops))
        lines.append(
            {
                "x": min(run["x"] for run in visible),
                "x_end": max(run["x"] + estimated_width(run) for run in visible),
                "y": min(run["y"] for run in visible),
                "size": max(run["size"] for run in visible),
                "tabular": bool(tab_stops),
                "tab_stops": tab_stops,
                "segments": segments,
                "width_original": width_original,
                "width_word": width_word,
            }
        )

    return lines


def line_widths(visible_runs, segments, tabular):
    """
    (original width, width in Word's substitute font) of one line, in px.

    The original width is measured from the first to the last glyph in the
    browser. The Word width adds the substitute font's glyph widths and the
    width of every space between runs. Returns (0, 0) when unknown.
    """
    if tabular or not visible_runs:
        return 0.0, 0.0
    if not all(float(run.get("vw") or 0) > 0 and float(run.get("fw") or 0) > 0 for run in visible_runs):
        return 0.0, 0.0

    left = min(float(run.get("vx") or run["x"]) for run in visible_runs)
    right = max(float(run.get("vx") or run["x"]) + float(run["vw"]) for run in visible_runs)
    glyphs = sum(float(run["fw"]) for run in visible_runs)
    inner_spaces = sum(
        run["text"].replace(" ", " ").strip().count(" ") for run in visible_runs
    )
    line_text = "".join(segment["text"] for segment in segments)
    boundary_spaces = max(0, line_text.count(" ") - inner_spaces)
    space_width = sum(float(run.get("sw") or 0) for run in visible_runs) / len(visible_runs)
    return max(0.0, right - left), glyphs + boundary_spaces * space_width


def estimated_width(run):
    """
    Width of a text run in px.

    Browsers report inflated rects for Scribd's heavily scaled spans, so cap
    the measured width with a generous per-character estimate.
    """
    text = run.get("text") or ""
    estimate = len(text.rstrip()) * 0.6 * float(run.get("size") or 0)
    measured = float(run.get("w") or 0)
    if measured <= 0:
        return estimate
    return min(measured, estimate) if estimate > 0 else measured


def build_paragraphs(lines, page_width=0):
    """
    Merge consecutive lines into paragraphs using spacing, size and indent.

    Returns a list of {"x", "y", "size", "centered", "segments"}.
    """
    paragraphs = []
    right_edge = max((line["x_end"] for line in lines), default=0)

    def line_text(line):
        return "".join(segment["text"] for segment in line["segments"])

    for line in lines:
        current = paragraphs[-1] if paragraphs else None
        if current is not None:
            first = current["lines"][0]
            previous = current["lines"][-1]
            size = max(line["size"], previous["size"])
            gap = line["y"] - previous["y"]
            same_size = abs(line["size"] - previous["size"]) <= 0.15 * size
            close = 0 < gap <= 1.8 * size
            is_list_item = current["list"]
            if is_list_item:
                # Wrapped lines of a list item hang under its text.
                aligned = first["x"] - size <= line["x"] <= first["x"] + 5 * size
            else:
                aligned = abs(line["x"] - previous["x"]) <= 1.5 * size
                # First line of a paragraph is often indented; the next
                # line starts further left.
                if len(current["lines"]) == 1 and first["x"] - 5 * size < line["x"] < first["x"]:
                    aligned = True
            # A line that stops well before the right edge ends its paragraph.
            left = min(entry["x"] for entry in current["lines"])
            previous_short = (
                right_edge > left
                and previous["x_end"] - left < 0.75 * (right_edge - left)
            )
            new_item = starts_with_list_marker(line_text(line))
            # "Heading: text" items: a bold lead-in after a finished sentence.
            lead_in = (
                line["segments"][0]["style"][3]
                and not previous["segments"][-1]["style"][3]
                and line_text(previous).rstrip()[-1:] in {".", "!", "?", ":"}
            )
            new_item = new_item or lead_in or line.get("tabular") or previous.get("tabular")
            if same_size and close and aligned and not previous_short and not new_item:
                current["lines"].append(line)
                continue

        paragraphs.append(
            {"lines": [line], "list": starts_with_list_marker(line_text(line))}
        )

    result = []
    for paragraph in paragraphs:
        paragraph_lines = paragraph["lines"]
        segments = []
        for index, line in enumerate(paragraph_lines):
            line_segments = [dict(segment) for segment in line["segments"]]
            if index > 0 and segments:
                joiner = "" if segments[-1]["text"].endswith("-") else " "
                if line_segments[0]["style"] == segments[-1]["style"]:
                    segments[-1]["text"] += joiner + line_segments.pop(0)["text"]
                else:
                    segments[-1]["text"] += joiner
            segments.extend(line_segments)

        tab_stops = [x for line in paragraph_lines for x in line.get("tab_stops", [])]
        centered = False
        if page_width > 0 and not tab_stops:
            size = max(line["size"] for line in paragraph_lines)
            centered = all(
                abs((line["x"] + line["x_end"]) / 2 - page_width / 2)
                <= 0.04 * page_width
                and line["x"] > 0.12 * page_width
                for line in paragraph_lines
            )
            if len(paragraph_lines) == 1:
                # A full-width line is body text, not a centred heading.
                line = paragraph_lines[0]
                centered = centered and line["x_end"] - line["x"] < 0.7 * page_width
            else:
                # Wrapped centred text has ragged left edges; justified or
                # left-aligned text shares one left edge.
                lefts = [line["x"] for line in paragraph_lines]
                centers = [(line["x"] + line["x_end"]) / 2 for line in paragraph_lines]
                centered = (
                    centered
                    and max(lefts) - min(lefts) > 0.5 * size
                    and max(centers) - min(centers) <= 0.03 * page_width
                )
            first_text = "".join(s["text"] for s in paragraph_lines[0]["segments"])
            centered = centered and not starts_with_list_marker(first_text)

        result.append(
            {
                "x": paragraph_lines[0]["x"],
                # Left edge of wrapped lines (for hanging / first-line indents).
                "x_wrap": (
                    min(line["x"] for line in paragraph_lines[1:])
                    if len(paragraph_lines) > 1
                    else paragraph_lines[0]["x"]
                ),
                "x_end": max(line["x_end"] for line in paragraph_lines),
                "y": paragraph_lines[0]["y"],
                "y_end": paragraph_lines[-1]["y"] + paragraph_lines[-1]["size"],
                "size": max(line["size"] for line in paragraph_lines),
                "line_count": len(paragraph_lines),
                # Original text width / width in Word's substitute font; the
                # DOCX condenses characters by this ratio to wrap like the original.
                "width_ratio": (
                    sum(line.get("width_original", 0) for line in paragraph_lines)
                    / sum(line.get("width_word", 0) for line in paragraph_lines)
                    if sum(line.get("width_word", 0) for line in paragraph_lines) > 0
                    else None
                ),
                "centered": centered,
                "tab_stops": tab_stops,
                "segments": segments,
            }
        )

    return result


def attach_image_bullets(paragraphs, images):
    """
    Turn tiny marker images into "•" bullets on the paragraphs they mark.

    Scribd often draws list bullets as small images cut from a sprite: one
    dot, or a narrow vertical strip holding the dots of several items. A
    narrow image just left of a paragraph's first line becomes a bullet.

    Returns:
        The images that were not used as bullets.
    """
    sizes = sorted(paragraph["size"] for paragraph in paragraphs)
    if not sizes:
        return list(images)
    body_size = sizes[len(sizes) // 2]

    remaining = []
    for image in images:
        if not (0 < image["w"] <= 1.5 * body_size):
            remaining.append(image)
            continue

        marked = [
            paragraph
            for paragraph in paragraphs
            if 0 <= paragraph["x"] - (image["x"] + image["w"]) <= 4 * paragraph["size"]
            and image["y"] - 0.8 * paragraph["size"]
            <= paragraph["y"]
            <= image["y"] + image["h"]
            and not starts_with_list_marker(paragraph_text(paragraph))
        ]
        if not marked:
            remaining.append(image)
            continue

        for paragraph in marked:
            first_style = paragraph["segments"][0]["style"]
            bullet_style = (first_style[0], first_style[1], first_style[2], False, False, 0.0)
            paragraph["segments"].insert(0, {"text": "• ", "style": bullet_style})
            if paragraph.get("line_count", 1) == 1:
                paragraph["x_wrap"] = paragraph["x"]
            paragraph["x"] = image["x"]
            paragraph["list"] = True

    return remaining


def is_opaque_image(src):
    """JPEG images have no transparency, so they fully hide what is below."""
    src = (src or "").lower()
    if src.startswith("data:"):
        return src.startswith("data:image/jpeg") or src.startswith("data:image/jpg")
    return urlparse(src).path.endswith((".jpg", ".jpeg"))


def remove_covered_runs(runs, images, page_width=0, page_height=0):
    """
    Drop text that the browser does not show because an image covers it.

    Scribd stacks layers (text z=2, images z=3, text z=4, ...). Edited PDFs
    often keep the old text in a lower layer under an opaque white patch,
    with the new text in a higher layer, which would otherwise come out
    twice ("City, State (or Remote) City, State"). Full-page scans are
    left alone so their OCR text layer is kept.
    """
    page_area = float(page_width or 0) * float(page_height or 0)
    covers = [
        image
        for image in images or []
        if image.get("z") is not None
        and (page_area <= 0 or image["w"] * image["h"] < 0.4 * page_area)
    ]
    runs = list(runs or [])
    if not covers:
        return runs

    def overlapped_by_higher_text(run, size):
        """Newer text drawn in a higher layer over the same spot."""
        start, end = run["x"], run["x"] + estimated_width(run)
        for other in runs:
            if (
                other is not run
                and (other.get("z") or 0) > run["z"]
                and (other.get("text") or "").strip()
                and abs(other["y"] - run["y"]) <= 0.5 * size
                and min(end, other["x"] + estimated_width(other)) - max(start, other["x"])
                > 0.3 * (end - start)
            ):
                return True
        return False

    visible = []
    for run in runs:
        z = run.get("z")
        if z is not None and run.get("text", "").strip():
            size = float(run.get("size") or 0)
            center_x = run["x"] + estimated_width(run) / 2
            center_y = run["y"] + 0.4 * size
            covering = [
                image
                for image in covers
                if image["z"] > z
                and image["x"] <= center_x <= image["x"] + image["w"]
                and image["y"] <= center_y <= image["y"] + image["h"]
            ]
            # JPEGs are opaque, so covered text is invisible. PNG/GIF may be
            # transparent, so only drop text that newer text replaces.
            if any(is_opaque_image(image.get("src")) for image in covering) or (
                covering and overlapped_by_higher_text(run, size)
            ):
                continue
        visible.append(run)
    return visible


def convert_line_images_to_text(runs, images, page_width=0):
    """
    Turn thin line images that sit on a text line into "____" text.

    Fill-in blanks ("coursework in ______.") are often drawn as a 1px
    image between two words. Long rules with no text beside them (section
    dividers) stay images.
    """
    runs = list(runs or [])
    remaining = []
    text_runs = [run for run in runs if (run.get("text") or "").strip()]

    for image in images or []:
        is_line = 0 < image["h"] <= 3 and image["w"] >= 3 * image["h"]
        if not is_line or (page_width and image["w"] > 0.4 * page_width):
            remaining.append(image)
            continue

        line_runs = [
            run
            for run in text_runs
            if run["y"] <= image["y"] <= run["y"] + 1.4 * float(run.get("size") or 0)
        ]
        neighbours = [
            run
            for run in line_runs
            if abs(run["x"] + estimated_width(run) - image["x"]) <= 3 * run["size"]
            or abs(run["x"] - (image["x"] + image["w"])) <= 3 * run["size"]
        ]
        if not neighbours:
            remaining.append(image)
            continue

        model = min(neighbours, key=lambda run: abs(run["x"] - image["x"]))
        size = float(model.get("size") or 0) or 1.0
        count = max(2, int(round(image["w"] / (0.5 * size))))
        runs.append(
            {
                **model,
                "text": "_" * count,
                "x": image["x"],
                "w": image["w"],
                "bold": False,
                "italic": False,
            }
        )

    return runs, remaining


def layout_page(page):
    """Turn one extracted page into ordered blocks (paragraphs and images)."""
    width = float(page.get("width") or 0)
    height = float(page.get("height") or 0)
    images = page.get("images") or []
    runs = remove_covered_runs(page.get("runs"), images, width, height)
    runs, images = convert_line_images_to_text(runs, images, width)
    paragraphs = build_paragraphs(build_lines(runs), width)
    images = attach_image_bullets(paragraphs, images)
    blocks = [{"type": "paragraph", **paragraph} for paragraph in paragraphs]
    for image in images:
        # Images that share vertical space with text - boxes and shading the
        # text is drawn on, or icons and figures beside it - must float at
        # their exact position instead of pushing the text down.
        behind = any(
            min(image["y"] + image["h"], paragraph["y_end"]) - max(image["y"], paragraph["y"]) > 1
            for paragraph in paragraphs
        )
        # Images side by side (diagrams, galleries) cannot be stacked inline.
        behind = behind or any(
            other is not image
            and min(image["y"] + image["h"], other["y"] + other["h"]) - max(image["y"], other["y"]) > 1
            for other in images
        )
        # Thin rules and dividers: as inline pictures Word would give each a
        # full text line of height, adding drift on every page.
        behind = behind or image["h"] <= 6
        blocks.append({"type": "image", **image, "behind": behind})
    blocks.sort(key=lambda block: (round(block["y"], 1), block["x"]))
    return blocks


def paragraph_text(paragraph):
    return "".join(segment["text"] for segment in paragraph["segments"])


def render_text(pages):
    """Render extracted pages as plain text with page markers."""
    parts = []
    for page in pages:
        paragraphs = [
            paragraph_text(block)
            for block in layout_page(page)
            if block["type"] == "paragraph"
        ]
        parts.append(f"===== Page {page.get('pageNum')} =====")
        parts.append("\n\n".join(paragraphs))
    return "\n\n".join(parts).rstrip() + "\n"


def write_text_file(pages, filename):
    """Write extracted pages as UTF-8 plain text (atomically)."""
    temp_name = f"{filename}.part"
    with open(temp_name, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_text(pages))
    os.replace(temp_name, filename)
    return os.path.abspath(filename)


def fetch_image_bytes(src, timeout_seconds=30):
    """Download an image used on a Scribd page (or decode a data: URL)."""
    if src.startswith("data:"):
        header, _, payload = src.partition(",")
        if ";base64" in header:
            return base64.b64decode(payload)
        return unquote(payload).encode("latin-1")

    request = urllib.request.Request(
        src,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) scribd-downloader",
            "Referer": "https://www.scribd.com/",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


BULLET_GLYPHS = {"•", "●", "▪", "■", "◦", "·", "‣", "⁃"}


def apply_picture_crop(picture, crop):
    """
    Crop an inline python-docx picture to the part Scribd actually shows.

    crop holds fractions (0-1) to cut from each side; Word stores them in
    a:srcRect as 1/1000 of a percent.
    """
    if not crop:
        return

    values = {
        side: int(round(max(0.0, min(1.0, float(crop.get(side) or 0))) * 100000))
        for side in ("left", "top", "right", "bottom")
    }
    if not any(values.values()):
        return

    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    blip_fill = picture._inline.graphic.graphicData.pic.blipFill
    src_rect = blip_fill.find(qn("a:srcRect"))
    if src_rect is None:
        src_rect = OxmlElement("a:srcRect")
        blip = blip_fill.find(qn("a:blip"))
        blip.addnext(src_rect)
    for side, attribute in (("left", "l"), ("top", "t"), ("right", "r"), ("bottom", "b")):
        if values[side]:
            src_rect.set(attribute, str(values[side]))


def crop_image_data(data, crop):
    """
    Cut an image down to the part Scribd shows (sprites are clipped with CSS).

    Word cannot reliably crop pictures itself: a JPEG cropped on both top and
    bottom to a sliver (e.g. a 1px rule from a sprite) is silently not drawn.
    So the pixels are cropped here with Pillow when it is installed.

    Returns:
        (image bytes, crop still to apply in Word or None)
    """
    if not crop or not any(float(crop.get(side) or 0) > 0 for side in ("left", "top", "right", "bottom")):
        return data, None
    try:
        from PIL import Image
    except ImportError:
        return data, crop

    try:
        image = Image.open(BytesIO(data))
        width, height = image.size
        left = int(round(float(crop.get("left") or 0) * width))
        top = int(round(float(crop.get("top") or 0) * height))
        right = width - int(round(float(crop.get("right") or 0) * width))
        bottom = height - int(round(float(crop.get("bottom") or 0) * height))
        right = max(right, min(width, left + 1))
        bottom = max(bottom, min(height, top + 1))
        cropped = image.crop((left, top, right, bottom))
        output = BytesIO()
        if image.format == "JPEG" and cropped.mode in ("RGB", "L", "CMYK"):
            cropped.save(output, format="JPEG", quality=95)
        else:
            cropped.save(output, format="PNG")
        return output.getvalue(), None
    except Exception:  # unreadable image: let Word try with a srcRect crop
        return data, crop


PX_TO_EMU = 9525  # 1 CSS px = 1/96 inch = 9525 EMU
PARAGRAPH_OFFSET_INCLUDES_SPACE = 1


# Run properties that must come after w:spacing / w:w (OOXML CT_RPr order).
_RPR_AFTER_SPACING = (
    "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight", "w:u", "w:effect",
    "w:bdr", "w:shd", "w:fitText", "w:vertAlign", "w:rtl", "w:cs", "w:em",
    "w:lang", "w:eastAsianLayout", "w:specVanish", "w:oMath",
)


def _set_run_property(run, tag, value, successors):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run_properties = run._r.get_or_add_rPr()
    element = run_properties.find(qn(tag))
    if element is None:
        element = OxmlElement(tag)
        run_properties.insert_element_before(element, *successors)
    element.set(qn("w:val"), str(value))


def set_character_spacing(run, spacing_px):
    """Word character spacing (Font > Advanced > Spacing), in 1/20 pt."""
    _set_run_property(
        run, "w:spacing", int(round(spacing_px * 0.75 * 20)), ("w:w",) + _RPR_AFTER_SPACING
    )


def set_character_scale(run, percent):
    """Word character scale (Font > Advanced > Scale), in percent."""
    _set_run_property(run, "w:w", int(percent), _RPR_AFTER_SPACING)


DOT_LEADER = re.compile(r"[ \t]*(?:\.[ ]?){4,}[ \t]*|[ \t]*…{2,}[ \t]*")


def float_picture_behind_text(picture, x_px, y_px, z_order, vertical_from="page"):
    """
    Turn an inline python-docx picture into a floating picture placed behind
    the text (Word "Behind Text" layout). x is measured from the page's left
    edge; y from the top of the page, or from the anchor paragraph when
    vertical_from="paragraph" so the picture moves with its text.
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    inline = picture._inline
    extent = inline.extent
    doc_pr = inline.docPr
    anchor = parse_xml(
        f'<wp:anchor {nsdecls("wp", "a", "pic", "r")} distT="0" distB="0" distL="0" '
        f'distR="0" simplePos="0" relativeHeight="{z_order}" behindDoc="1" locked="0" '
        'layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        '<wp:positionH relativeFrom="page">'
        f"<wp:posOffset>{int(x_px * PX_TO_EMU)}</wp:posOffset></wp:positionH>"
        f'<wp:positionV relativeFrom="{vertical_from}">'
        f"<wp:posOffset>{int(y_px * PX_TO_EMU)}</wp:posOffset></wp:positionV>"
        f'<wp:extent cx="{extent.cx}" cy="{extent.cy}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        "<wp:wrapNone/>"
        f'<wp:docPr id="{doc_pr.id}" name="{doc_pr.name}"/>'
        "<wp:cNvGraphicFramePr/>"
        "</wp:anchor>"
    )
    anchor.append(inline.graphic)
    inline.getparent().replace(inline, anchor)
    return anchor


def footer_key(block):
    """Footer text with numbers ignored, so "Page 3" and "Page 4" match."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", paragraph_text(block))).strip().lower()


def find_repeating_footers(pages, page_blocks, zone=0.85):
    """
    Find page-footer lines: short text at the bottom of the page that repeats
    on many pages (running titles, copyright lines, page numbers).

    Returns:
        A set of footer_key() values.
    """
    if len(pages) < 3:
        return set()

    counts = {}
    for page, blocks in zip(pages, page_blocks):
        height = float(page.get("height") or 0)
        if height <= 0:
            continue
        seen = set()
        for block in reversed([b for b in blocks if not b.get("behind")]):
            if (
                block["type"] != "paragraph"
                or block["y"] < zone * height
                or block.get("line_count", 1) > 2
            ):
                break
            seen.add(footer_key(block))
        for key in seen:
            counts[key] = counts.get(key, 0) + 1

    minimum = max(2, int(0.2 * len(pages)))
    return {key for key, count in counts.items() if key and count >= minimum}


def write_docx_file(pages, filename, image_fetcher=fetch_image_bytes):
    """
    Write extracted pages as an editable Word document.

    Each Scribd page becomes one Word section with the same paper size and
    margins taken from where the text sits on that page. Text keeps Scribd's
    fonts, sizes, colours, bold/italic, letter spacing, indents and
    tab-aligned columns; character widths are scaled to the original so lines
    wrap in the same places; vertical gaps and line heights are copied from
    the original so pages do not overflow. Page footers go into the real Word
    footer. Figures become pictures; images that share space with text
    (boxes, shading, icons) float at their exact position.
    """
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.text import (
        WD_ALIGN_PARAGRAPH,
        WD_LINE_SPACING,
        WD_TAB_ALIGNMENT,
        WD_TAB_LEADER,
    )
    from docx.shared import Pt, RGBColor

    min_margin_px = 0.15 * 96
    document = Document()
    image_cache = {}
    failed = {"images": 0}
    z_order = 0

    def load_image(src):
        if src not in image_cache:
            try:
                image_cache[src] = image_fetcher(src)
            except Exception:  # network errors, bad URLs, ...
                image_cache[src] = None
        return image_cache[src]

    def clamp(value, low, high):
        return max(low, min(high, value))

    def px(value):
        return Pt(value * 0.75)

    def new_paragraph(container, reuse):
        """Use the empty paragraph a new header/footer starts with, once."""
        if reuse and container.paragraphs and not container.paragraphs[0].text:
            paragraph = container.paragraphs[0]
            if not paragraph.runs:
                return paragraph
        return container.add_paragraph()

    def add_image_paragraph(container, block, gap, area, reuse=False):
        """Inline picture in its own paragraph. Returns its height or None."""
        data = load_image(block["src"])
        if not data:
            failed["images"] += 1
            return None
        scale = min(1.0, area["content_width"] / block["w"])
        width, height = block["w"] * scale, block["h"] * scale
        paragraph = new_paragraph(container, reuse)
        paragraph_format = paragraph.paragraph_format
        paragraph_format.space_before = px(min(gap, area["page_height"]))
        paragraph_format.space_after = Pt(0)
        paragraph_format.line_spacing = 1.0
        indent = clamp(block["x"] - area["left"], 0.0, area["content_width"] * 0.8)
        if indent + width <= area["content_width"]:
            paragraph_format.left_indent = px(indent)
        data, crop = crop_image_data(data, block.get("crop"))
        try:
            picture = paragraph.add_run().add_picture(
                BytesIO(data), width=px(width), height=px(height)
            )
            apply_picture_crop(picture, crop)
        except Exception:  # unsupported image format
            failed["images"] += 1
            paragraph._element.getparent().remove(paragraph._element)
            return None
        return height

    def add_text_paragraph(container, block, gap, area, reuse=False):
        """One text paragraph. Returns (paragraph, height in px)."""
        size = block["size"]
        line_count = block.get("line_count", 1)
        line_height = size * 1.2
        if line_count > 1:
            pitch = (block["y_end"] - size - block["y"]) / (line_count - 1)
            if size <= pitch <= 3 * size:
                line_height = pitch
        line_height = max(line_height, size * 1.05)

        paragraph = new_paragraph(container, reuse)
        paragraph_format = paragraph.paragraph_format
        paragraph_format.space_before = px(min(gap, area["page_height"]))
        paragraph_format.space_after = Pt(0)
        paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        paragraph_format.line_spacing = px(line_height)

        left, content_width, right_edge = area["left"], area["content_width"], area["right_edge"]
        indent = clamp(block["x"] - left, 0.0, content_width * 0.8)
        if block["centered"]:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        else:
            wrap_indent = clamp(block.get("x_wrap", block["x"]) - left, 0.0, content_width * 0.8)
            if wrap_indent > 0.5:
                paragraph_format.left_indent = px(wrap_indent)
            if abs(indent - wrap_indent) > 0.5:
                # Negative = hanging indent (bullets), positive = first-line indent.
                paragraph_format.first_line_indent = px(indent - wrap_indent)
            if line_count > 1:
                # Wrap where the original wrapped (e.g. text inside a box),
                # with the same slack as the page's right margin.
                right_indent = left + content_width - (block["x_end"] + area["slack"])
                if right_indent > size:
                    paragraph_format.right_indent = px(right_indent)

        tab_stops = block.get("tab_stops") or []
        for index, tab_x in enumerate(tab_stops):
            if index == len(tab_stops) - 1 and block["x_end"] >= right_edge - 2 * size:
                # Right-aligned column, e.g. dates or "City, State".
                position = min(right_edge - left, content_width)
                paragraph_format.tab_stops.add_tab_stop(px(position), WD_TAB_ALIGNMENT.RIGHT)
            else:
                position = clamp(tab_x - left, 0.0, content_width)
                paragraph_format.tab_stops.add_tab_stop(px(position))

        # Table-of-contents lines: "Title ........ 23" -> a tab with a dot
        # leader, like a real Word TOC, so the dots never wrap.
        segments = [dict(segment) for segment in block["segments"]]
        full_text = "".join(segment["text"] for segment in segments)
        leader = DOT_LEADER.search(full_text)
        # Condense characters (never stretch) when Word's substitute font is
        # wider than Scribd's embedded font, so lines wrap where they did.
        width_ratio = block.get("width_ratio")
        character_scale = None
        if width_ratio and not tab_stops and not leader:
            scale = 100.0 * width_ratio
            if scale <= 97.0:
                character_scale = round(clamp(scale, 50.0, 100.0))
        if leader and full_text[leader.end():].strip() and full_text[: leader.start()].strip():
            for segment in segments:
                if DOT_LEADER.search(segment["text"]):
                    segment["text"] = DOT_LEADER.sub("\t", segment["text"], count=1)
                    segment["text"] = re.sub(r"\t\s*\t", "\t", segment["text"])
                    break
            position = min(right_edge - left, content_width)
            paragraph_format.tab_stops.add_tab_stop(
                px(position), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS
            )

        for segment in segments:
            font_name, font_size, color, bold, italic, spacing = segment["style"]
            is_bullet = segment["text"].strip() in BULLET_GLYPHS
            if is_bullet:
                # Scribd often draws bullets in a tiny symbol font.
                font_size = max(font_size, size * 0.8)
            run = paragraph.add_run(segment["text"])
            if font_name:
                run.font.name = font_name
            if font_size:
                run.font.size = Pt(max(1.0, min(400.0, font_size * 0.75)))
            if color:
                run.font.color.rgb = RGBColor.from_string(color)
            run.font.bold = bold or None
            run.font.italic = italic or None
            if spacing:
                set_character_spacing(run, spacing)
            if character_scale and not is_bullet and not spacing:
                set_character_scale(run, character_scale)

        return paragraph, line_count * line_height

    def write_blocks(container, blocks, top, area, reuse=False):
        """
        Write flowing blocks top to bottom.

        Returns a list of (paragraph, block y, space before) for anchoring
        floating images to the text next to them.
        """
        cursor = top  # where Word's next line starts, in page px
        written = []
        for block in blocks:
            gap = max(0.0, block["y"] - cursor)
            reuse_first = reuse and not written
            if block["type"] == "image":
                height = add_image_paragraph(container, block, gap, area, reuse_first)
                if height is None:
                    continue
                paragraph = container.paragraphs[0] if reuse_first else container.paragraphs[-1]
            else:
                paragraph, height = add_text_paragraph(container, block, gap, area, reuse_first)
            written.append((paragraph, block["y"], gap, block))
            cursor = max(cursor, block["y"]) + height
        return written

    page_blocks = [layout_page(page) for page in pages]
    footer_texts = find_repeating_footers(pages, page_blocks)

    for page_index, page in enumerate(pages):
        width_px = float(page.get("width") or 0) or DEFAULT_PAPER_WIDTH_INCHES * 96
        height_px = float(page.get("height") or 0) or DEFAULT_PAPER_HEIGHT_INCHES * 96
        blocks = page_blocks[page_index]
        flow = [block for block in blocks if not block.get("behind")]

        # Page footers (running titles, copyright lines, page numbers that
        # repeat at the bottom of many pages) go into the real Word footer,
        # so they can never be pushed onto an extra page.
        footer_blocks = []
        for block in reversed(flow):
            if block["type"] == "paragraph" and footer_key(block) in footer_texts:
                footer_blocks.insert(0, block)
            else:
                break
        body = flow[: len(flow) - len(footer_blocks)]

        left_edge = min((block["x"] for block in flow), default=48.0)
        right_edge = max(
            (
                block["x_end"] if block["type"] == "paragraph" else block["x"] + block["w"]
                for block in flow
            ),
            default=width_px - 48.0,
        )
        top_edge = min((block["y"] for block in body), default=48.0)

        # Margins follow the original text area. The right margin gets a
        # little slack because substitute fonts are rarely an exact match.
        slack = 0.02 * width_px
        left_margin = clamp(left_edge, min_margin_px, width_px * 0.4)
        right_margin = clamp(width_px - right_edge - slack, min_margin_px, width_px * 0.4)
        top_margin = clamp(top_edge, min_margin_px, height_px * 0.4)
        bottom_margin = min_margin_px
        if footer_blocks:
            bottom_margin = clamp(height_px - footer_blocks[0]["y"], min_margin_px, height_px * 0.4)
        content_width = max(96.0, width_px - left_margin - right_margin)
        area = {
            "left": left_margin,
            "content_width": content_width,
            "right_edge": right_edge,
            "page_height": height_px,
            "slack": slack,
        }

        section = (
            document.sections[0]
            if page_index == 0
            else document.add_section(WD_SECTION.NEW_PAGE)
        )
        section.page_width = px(width_px)
        section.page_height = px(height_px)
        section.left_margin = px(left_margin)
        section.right_margin = px(right_margin)
        section.top_margin = px(top_margin)
        section.bottom_margin = px(bottom_margin)
        section.header_distance = px(min_margin_px)

        footer = section.footer
        footer.is_linked_to_previous = False
        if footer_blocks:
            footer_top = footer_blocks[0]["y"]
            footer_bottom = max(
                block.get("y_end", block["y"] + block.get("h", 0)) for block in footer_blocks
            )
            section.footer_distance = px(clamp(height_px - footer_bottom - 4, 0.0, bottom_margin))
            write_blocks(footer, footer_blocks, footer_top, area, reuse=True)
        else:
            section.footer_distance = px(min_margin_px / 2)

        written = write_blocks(document, body, top_margin, area)
        if not written:
            written = [(document.add_paragraph(), top_margin, 0.0, None)]

        # Background and side images (boxes, rules, icons, diagrams) float at
        # their original spot. Each is anchored to the paragraph just above
        # it and positioned relative to that paragraph, so if Word sets the
        # text a little higher or lower, the picture moves with its text.
        for block in blocks:
            if not block.get("behind"):
                continue
            data = load_image(block["src"])
            if not data:
                failed["images"] += 1
                continue
            above = [entry for entry in written if entry[1] <= block["y"] + 2]
            paragraph, paragraph_y, space_before, anchor = above[-1] if above else written[0]
            # Word measures paragraph-relative offsets from the top of the
            # paragraph including its space before.
            offset = block["y"] - paragraph_y + space_before * PARAGRAPH_OFFSET_INCLUDES_SPACE
            if anchor is not None and anchor["type"] == "paragraph" and block["h"] <= 6:
                # A rule just under a line of text: substitute fonts are often
                # a little taller, so keep the rule clear of the descenders.
                below_text = block["y"] - anchor["y_end"]
                if 0 <= below_text <= 0.6 * anchor["size"]:
                    offset += 0.15 * anchor["size"]
            data, crop = crop_image_data(data, block.get("crop"))
            try:
                picture = paragraph.add_run().add_picture(
                    BytesIO(data), width=px(block["w"]), height=px(block["h"])
                )
                apply_picture_crop(picture, crop)
                z_order += 1
                float_picture_behind_text(
                    picture, block["x"], offset, z_order, vertical_from="paragraph"
                )
            except Exception:  # unsupported image format
                failed["images"] += 1

    temp_name = f"{filename}.part"
    document.save(temp_name)
    os.replace(temp_name, filename)

    if failed["images"]:
        print(f"  Warning: {failed['images']} image(s) could not be added to the DOCX.")

    return os.path.abspath(filename)


def export_single_page(driver, index, page_count, spool_dir):
    """
    Print one Scribd page to a one-sheet PDF in spool_dir.

    Returns:
        The page PDF path, or None if the page had to be skipped.
    """
    from pypdf import PdfReader

    page_info = driver.execute_script(
        """
        const targetIndex = arguments[0];

        const pages = Array.from(
            document.querySelectorAll(
                '.outer_page'
            )
        );

        const target = pages[targetIndex];

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

                .outer_page[
                    data-export-target="true"
                ] {
                    display: block !important;
                    visibility: visible !important;

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
        index,
    )

    if not page_info:
        print(
            f"  Skipping page "
            f"{index + 1}: element missing"
        )
        return None

    width_px = int(page_info["width"])
    height_px = int(page_info["height"])

    if width_px <= 0 or height_px <= 0:
        print(
            f"  Skipping page {index + 1}: "
            f"invalid geometry "
            f"{width_px}x{height_px}"
        )
        return None

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
        spool_dir,
        f"page-{index + 1:08d}.pdf",
    )
    with open(page_path, "wb") as page_handle:
        page_handle.write(pdf_bytes)

    print(
        f"    OK: exactly 1 PDF sheet"
    )
    return page_path


def save_pdf_pages_individually(
    driver,
    filename,
    timeout_seconds=DEFAULT_CDP_TIMEOUT_SECONDS,
    write_pdf=True,
    on_batch_loaded=None,
):
    """
    Walk the document in bounded batches: load, (print), release.

    Args:
        write_pdf: When False, pages are loaded and released but not printed
            (used for TXT/DOCX-only exports).
        on_batch_loaded: Optional callback receiving the list of 1-based page
            numbers right after each batch is loaded (used to extract text).

    Returns:
        The absolute PDF path, or None when write_pdf is False.
    """
    from pypdf import PdfWriter

    configure_command_timeout(
        driver,
        timeout_seconds,
    )

    page_count = driver.execute_script(
        """
        return document.querySelectorAll(
            '.outer_page'
        ).length;
        """
    )

    if page_count <= 0:
        raise RuntimeError(
            "No .outer_page elements found."
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
                batch_page_numbers = list(
                    range(index + 1, batch_end + 1)
                )
                print(
                    f"  Loading page batch "
                    f"{index + 1}-{batch_end}/{page_count}..."
                )
                load_page_batch(
                    driver,
                    batch_page_numbers,
                )
                if on_batch_loaded is not None:
                    on_batch_loaded(batch_page_numbers)

            if write_pdf:
                page_path = export_single_page(
                    driver,
                    index,
                    page_count,
                    spool.name,
                )
                if page_path:
                    page_files.append(page_path)

            is_batch_end = (
                (index + 1) % DEFAULT_EXPORT_BATCH_SIZE == 0
                or index + 1 == page_count
            )
            if is_batch_end:
                release_page_batch(
                    driver,
                    batch_page_numbers,
                )

        if not write_pdf:
            return None

        if not page_files:
            raise RuntimeError(
                "No valid document pages "
                "were exported."
            )

        print(
            f"Merging {len(page_files)} "
            "disk-spooled PDF pages..."
        )
        temp_name = f"{filename}.part"
        writer = PdfWriter()
        try:
            for page_path in page_files:
                writer.append(page_path)
            with open(temp_name, "wb") as output_handle:
                writer.write(output_handle)
        finally:
            writer.close()
        os.replace(temp_name, filename)

    finally:
        spool.cleanup()

    return os.path.abspath(filename)


def parse_formats(value):
    """Parse '--format pdf,docx' into an ordered, de-duplicated tuple."""
    requested = [part.strip().lower() for part in (value or "").split(",") if part.strip()]
    if not requested:
        raise argparse.ArgumentTypeError("at least one format is required")

    if "all" in requested:
        return SUPPORTED_FORMATS

    unknown = [part for part in requested if part not in SUPPORTED_FORMATS]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unsupported format(s): {', '.join(unknown)} "
            f"(choose from {', '.join(SUPPORTED_FORMATS)} or all)"
        )

    return tuple(dict.fromkeys(requested))


def read_links_from_text(text):
    """Split text into link candidates: whitespace separated, '#' comments ignored."""
    links = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        links.extend(line.split())
    return links


def plan_downloads(urls):
    """
    Validate links, drop duplicates and give every document a unique name.

    Returns:
        (jobs, invalid_urls) where each job is
        {"url", "info", "base_name"}.
    """
    jobs = []
    invalid = []
    seen_ids = set()
    used_names = set()

    for url in urls:
        info = parse_scribd_url(url)
        if not info:
            invalid.append(url)
            continue

        if info["doc_id"] in seen_ids:
            continue
        seen_ids.add(info["doc_id"])

        base_name = get_base_name_from_url(url)
        if base_name.lower() in used_names:
            base_name = f"{base_name}-{info['doc_id']}"
        used_names.add(base_name.lower())

        jobs.append({"url": url, "info": info, "base_name": base_name})

    return jobs, invalid


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="scribd-downloader.py",
        description="Download Scribd documents as PDF, TXT or DOCX.",
        epilog=(
            "examples:\n"
            "  python scribd-downloader.py\n"
            "  python scribd-downloader.py https://www.scribd.com/document/123/Title\n"
            "  python scribd-downloader.py URL1 URL2 --format pdf,docx\n"
            "  python scribd-downloader.py --file links.txt -o downloads\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "urls",
        nargs="*",
        metavar="URL",
        help="Scribd document link(s). If none are given you will be prompted.",
    )
    parser.add_argument(
        "-f",
        "--file",
        action="append",
        default=[],
        metavar="PATH",
        help="Text file with Scribd links, one per line ('#' starts a comment). "
        "Use '-' to read from standard input. Can be repeated.",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        type=parse_formats,
        default=("pdf",),
        metavar="FORMATS",
        help="Comma-separated output formats: pdf, txt, docx, or all (default: pdf).",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Folder to save files in (default: current folder).",
    )
    return parser


def collect_urls(args, input_func=input, stdin=None):
    """Gather links from arguments, --file inputs, or an interactive prompt."""
    urls = list(args.urls)

    for path in args.file:
        if path == "-":
            urls.extend(read_links_from_text((stdin or sys.stdin).read()))
        else:
            with open(path, encoding="utf-8-sig") as handle:
                urls.extend(read_links_from_text(handle.read()))

    if not urls and not args.file:
        urls = read_links_from_text(input_func("Input link Scribd: "))

    return urls


def download_document(job, formats, output_dir):
    """
    Export one Scribd document in the requested formats.

    Returns:
        Dict mapping format -> saved absolute path.
    """
    info = job["info"]
    converted_url = info["embed_url"]
    output_paths = {
        fmt: os.path.join(output_dir, f"{job['base_name']}.{fmt}")
        for fmt in formats
    }

    print(f"Link embed: {redact_url(converted_url)}")
    for path in output_paths.values():
        print(f"Output filename: {path if output_dir != '.' else os.path.basename(path)}")

    saved = {}
    extracted_pages = []
    wants_content = any(fmt in output_paths for fmt in ("txt", "docx"))

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
            detected = wait_for_document_ready(driver)
            print(f"Viewer ready: {detected} page(s) detected.")

            hide_cookie_dialogs(driver)
            print("Cookie dialogs hidden.")

            total_pages = driver.execute_script(
                """
                return document.querySelectorAll('.outer_page').length;
                """
            )

            if total_pages == 0:
                raise RuntimeError(
                    diagnose_load_failure(
                        driver,
                        os.path.join(output_dir, f"scribd-debug-{info['doc_id']}"),
                    )
                )

            prepare_document_for_print(driver)

            inject_print_styles(driver)

            if "pdf" in output_paths:
                print(
                    f"\nSaving PDF as: {output_paths['pdf']}"
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
            else:
                print("\nReading document text and images...")

            driver.execute_script(
                "window.scrollTo(0, 0)"
            )

            def collect_batch(page_numbers):
                extracted_pages.extend(
                    extract_page_content(driver, page_numbers)
                )

            saved_path = save_pdf_pages_individually(
                driver,
                output_paths.get("pdf", ""),
                write_pdf="pdf" in output_paths,
                on_batch_loaded=collect_batch if wants_content else None,
            )

            if "pdf" in output_paths:
                if not saved_path:
                    raise RuntimeError(
                        "PDF export failed."
                    )
                saved["pdf"] = saved_path
                print(
                    "PDF saved successfully to: "
                    f"{saved_path}"
                )

        finally:
            if driver is not None:
                driver.quit()
                print("Browser closed.")

    if "txt" in output_paths:
        saved["txt"] = write_text_file(extracted_pages, output_paths["txt"])
        print(f"TXT saved successfully to: {saved['txt']}")

    if "docx" in output_paths:
        print("Building DOCX (downloading page images)...")
        saved["docx"] = write_docx_file(extracted_pages, output_paths["docx"])
        print(f"DOCX saved successfully to: {saved['docx']}")

    return saved


def print_invalid_url_help(invalid_urls):
    for url in invalid_urls:
        print(f"Error: Not a valid Scribd document URL: {redact_url(url)}")
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


def main(argv=None):
    """Run the exporter from the command line or interactively."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if "docx" in args.formats:
        try:
            import docx  # noqa: F401
        except ImportError:
            parser.error(
                "DOCX export needs python-docx. Install it with: "
                "pip install -r requirements.txt"
            )

    try:
        urls = collect_urls(args)
    except OSError as error:
        parser.error(f"could not read links file: {error}")

    jobs, invalid_urls = plan_downloads(urls)

    if invalid_urls:
        print_invalid_url_help(invalid_urls)

    if not jobs:
        if not invalid_urls:
            print("Error: Please provide a valid Scribd document URL")
        raise SystemExit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    failures = []
    successes = []

    for number, job in enumerate(jobs, start=1):
        if len(jobs) > 1:
            print(f"\n=== Document {number}/{len(jobs)}: {job['base_name']} ===")
        try:
            download_document(job, args.formats, args.output_dir)
            successes.append(job)
        except (
            RuntimeError,
            WebDriverException,
            OSError,
        ) as error:
            print(
                f"Export failed: {error}"
            )
            failures.append((job, error))

    if len(jobs) > 1:
        print(
            f"\nSummary: {len(successes)} succeeded, {len(failures)} failed"
            + (f", {len(invalid_urls)} invalid link(s) skipped" if invalid_urls else "")
            + "."
        )
        for job, error in failures:
            first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
            print(f"  FAILED {job['base_name']}: {first_line}")

    if failures or invalid_urls:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
