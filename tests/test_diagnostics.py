"""Issue #7: wait for the viewer instead of sleeping, explain empty results."""

import pytest
from selenium.common.exceptions import WebDriverException


class FakeDriver:
    """Minimal stand-in for a Selenium driver."""

    def __init__(self, counts=(), title="", url="", body="", html="<html></html>"):
        self._counts = list(counts)
        self.title = title
        self.current_url = url
        self._body = body
        self.page_source = html
        self.screenshots = []

    def execute_script(self, script, *args):
        if "innerText" in script:
            return self._body
        value = self._counts.pop(0) if len(self._counts) > 1 else self._counts[0]
        if isinstance(value, Exception):
            raise value
        return value

    def save_screenshot(self, path):
        self.screenshots.append(path)
        with open(path, "wb") as handle:
            handle.write(b"png")
        return True


def test_waits_until_page_count_is_stable(sd):
    driver = FakeDriver(counts=[0, 0, 3, 8, 8, 8, 8])
    assert sd.wait_for_document_ready(driver, timeout_seconds=5, poll_seconds=0) == 8


def test_survives_script_errors_while_page_loads(sd):
    driver = FakeDriver(counts=[WebDriverException("not ready"), 5, 5, 5])
    assert sd.wait_for_document_ready(driver, timeout_seconds=5, poll_seconds=0) == 5


def test_gives_up_after_timeout(sd):
    driver = FakeDriver(counts=[0])
    assert sd.wait_for_document_ready(driver, timeout_seconds=0, poll_seconds=0) == 0


@pytest.mark.parametrize(
    "title, body, url, expected",
    [
        ("Just a moment...", "", "", "bot check"),
        ("", "Verify you are human by completing the action below.", "", "bot check"),
        ("Access denied", "", "", "denied access"),
        ("Page not found", "", "", "not found"),
        # Exactly what scribd.com shows for a deleted / missing document.
        ("Scribd", "Document Not Found\nSorry, we can't display this document.", "", "not found"),
        ("Scribd", "", "https://www.scribd.com/embeds/1/content?secret_password=x", "secret_password"),
        ("Scribd", "This document is private", "", "private"),
        ("Log in", "", "https://www.scribd.com/login", "login"),
        ("Scribd", "", "https://www.scribd.com/embeds/1/content", "did not load any pages"),
    ],
)
def test_classify_load_failure(sd, title, body, url, expected):
    assert expected in sd.classify_load_failure(title, body, url)


def test_diagnose_load_failure_saves_debug_files_and_redacts(sd, tmp_path):
    driver = FakeDriver(
        counts=[0],
        title="Just a moment...",
        url="https://www.scribd.com/embeds/1/content?secret_password=TOPSECRET",
        html="<html>challenge</html>",
    )
    message = sd.diagnose_load_failure(driver, str(tmp_path / "scribd-debug-1"))

    assert message.startswith("No printable document pages were detected.")
    assert "Cloudflare" in message
    assert "TOPSECRET" not in message
    assert (tmp_path / "scribd-debug-1.png").read_bytes() == b"png"
    assert (tmp_path / "scribd-debug-1.html").read_text(encoding="utf-8") == "<html>challenge</html>"
