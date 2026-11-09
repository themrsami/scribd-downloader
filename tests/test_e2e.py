"""
Live end-to-end test against scribd.com (needs Chrome and internet).

Skipped by default. Run with:
    SCRIBD_E2E=1 python -m pytest -m e2e -v
Optionally pick the documents with SCRIBD_E2E_URLS (space separated).
"""

import os
import subprocess
import sys

import pytest
from docx import Document
from pypdf import PdfReader

from conftest import SCRIPT, load_module

DEFAULT_URLS = [
    # Locale subdomain (issue #13), 8 pages.
    "https://de.scribd.com/document/713748124/1-Page-Resume-Template",
    # No scheme, no www; tab-aligned columns and bullet lists, 2 pages.
    "scribd.com/document/523403201/Resume-Template",
]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.getenv("SCRIBD_E2E") != "1", reason="set SCRIBD_E2E=1 to run"),
]


def test_bulk_download_all_formats(tmp_path):
    urls = os.getenv("SCRIBD_E2E_URLS", "").split() or DEFAULT_URLS
    links_file = tmp_path / "links.txt"
    links_file.write_text("# e2e\n" + "\n".join(urls) + "\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    env = dict(os.environ, SCRIBD_EXPORT_BATCH_SIZE="3", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(links_file), "--format", "all", "-o", str(out_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=900,
    )
    print(result.stdout[-4000:])
    print(result.stderr[-4000:])
    assert result.returncode == 0

    sd = load_module()
    for url in urls:
        base = out_dir / sd.get_base_name_from_url(url)
        pdf = PdfReader(f"{base}.pdf")
        assert len(pdf.pages) >= 1
        assert f"Exporting {len(pdf.pages)} document pages" in result.stdout

        text = (base.with_suffix(".txt")).read_text(encoding="utf-8")
        assert text.count("===== Page ") == len(pdf.pages)
        assert len(text.split()) > 50

        document = Document(f"{base}.docx")
        docx_words = sum(len(p.text.split()) for p in document.paragraphs)
        assert docx_words > 50
