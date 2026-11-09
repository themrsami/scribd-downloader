"""Issue #6: TXT and DOCX files written from extracted Scribd pages."""

import os

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Pt

from conftest import make_png, run


def sample_pages():
    return [
        {
            "pageNum": 1,
            "width": 960,
            "height": 1248,
            "runs": [
                run("Report Title", 380, 60, size=32, w=200, font="Georgia", bold=True),
                run("Name:", 60, 200, w=60, bold=True, color="rgb(200, 0, 0)"),
                run(" Jane Doe", 122, 200, w=90),
                run("City, State", 800, 200, w=100),
                run("•", 60, 260, size=6, w=4),
                run("First point", 80, 257, w=120),
            ],
            "images": [
                {
                    "src": "https://example.test/sprite.png",
                    "x": 60,
                    "y": 300,
                    "w": 400,
                    "h": 20,
                    "crop": {"left": 0.0, "top": 0.25, "right": 0.5, "bottom": 0.25},
                }
            ],
        },
        {
            "pageNum": 2,
            "width": 1248,
            "height": 960,  # landscape page -> new section
            "runs": [run("Second page", 60, 60, w=200, italic=True)],
            "images": [],
        },
    ]


def test_write_text_file(sd, tmp_path):
    target = tmp_path / "doc.txt"
    path = sd.write_text_file(sample_pages(), str(target))

    assert path == str(target.resolve())
    assert not os.path.exists(f"{target}.part")
    assert target.read_text(encoding="utf-8") == (
        "===== Page 1 =====\n\n"
        "Report Title\n\n"
        "Name: Jane Doe\tCity, State\n\n"
        "• First point\n\n"
        "===== Page 2 =====\n\n"
        "Second page\n"
    )


def test_write_docx_file_keeps_text_styles_layout_and_images(sd, tmp_path):
    fetched = []

    def fake_fetch(src):
        fetched.append(src)
        return make_png(40, 20)

    target = tmp_path / "doc.docx"
    sd.write_docx_file(sample_pages(), str(target), image_fetcher=fake_fetch)
    assert not os.path.exists(f"{target}.part")

    document = Document(str(target))
    paragraphs = [p for p in document.paragraphs if p.text.strip()]
    assert [p.text for p in paragraphs] == [
        "Report Title",
        "Name: Jane Doe\tCity, State",
        "• First point",
        "Second page",
    ]

    title = paragraphs[0]
    assert title.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs[0].font.name == "Georgia"
    assert title.runs[0].font.size == Pt(24)  # 32px * 0.75
    assert title.runs[0].font.bold is True

    name_run = paragraphs[1].runs[0]
    assert name_run.text == "Name:"
    assert name_run.font.bold is True
    assert str(name_run.font.color.rgb) == "C80000"
    tab_stop = paragraphs[1].paragraph_format.tab_stops[0]
    assert tab_stop.alignment == WD_TAB_ALIGNMENT.RIGHT

    bullet_run = paragraphs[2].runs[0]
    assert bullet_run.text.strip() == "•"
    # Tiny 6px bullet enlarged to ~80% of the text size (Word rounds to 0.5pt).
    assert bullet_run.font.size >= Pt(9)

    assert paragraphs[3].runs[0].font.italic is True

    # One page per Scribd page: the landscape page starts a new section.
    assert len(document.sections) == 2
    assert document.sections[0].page_width == pytest.approx(Pt(960 * 0.75), abs=Pt(1))
    assert document.sections[1].page_width > document.sections[1].page_height

    # Image downloaded once and cropped to the visible sprite area
    # (40x20 sprite, cut 25% top/bottom and 50% right -> 20x10).
    assert fetched == ["https://example.test/sprite.png"]
    assert len(document.inline_shapes) == 1
    blip = document.element.body.find(".//" + qn("a:blip"))
    image = document.part.related_parts[blip.get(qn("r:embed"))].image
    assert (image.px_width, image.px_height) == (20, 10)
    assert document.element.body.find(".//" + qn("a:srcRect")) is None


def test_crop_image_data_cuts_pixels(sd):
    from io import BytesIO

    from PIL import Image

    # A 1px rule cut from the middle of a JPEG sprite: Word would not draw
    # this if it had to crop it itself.
    sprite = BytesIO()
    Image.new("RGB", (700, 6), (0, 90, 170)).save(sprite, format="JPEG")
    data, remaining = sd.crop_image_data(
        sprite.getvalue(), {"left": 0.0, "top": 1 / 6, "right": 0.0, "bottom": 4 / 6}
    )
    assert remaining is None
    cropped = Image.open(BytesIO(data))
    assert cropped.format == "JPEG"
    assert cropped.size == (700, 1)

    png = make_png(40, 20)
    data, remaining = sd.crop_image_data(png, {"left": 0.5})
    assert Image.open(BytesIO(data)).size == (20, 20) and remaining is None
    assert sd.crop_image_data(png, None) == (png, None)
    assert sd.crop_image_data(png, {"top": 0}) == (png, None)


def test_crop_falls_back_to_word_crop_without_pillow(sd, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pillow(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no Pillow")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pillow)
    crop = {"top": 0.25}
    assert sd.crop_image_data(make_png(), crop) == (make_png(), crop)


def test_docx_survives_image_download_failures(sd, tmp_path, capsys):
    def broken_fetch(src):
        raise OSError("network down")

    target = tmp_path / "doc.docx"
    sd.write_docx_file(sample_pages(), str(target), image_fetcher=broken_fetch)

    document = Document(str(target))
    assert len(document.inline_shapes) == 0
    assert "Report Title" in [p.text for p in document.paragraphs]
    assert "1 image(s) could not be added" in capsys.readouterr().out


def test_docx_skips_unsupported_image_data(sd, tmp_path, capsys):
    target = tmp_path / "doc.docx"
    sd.write_docx_file(sample_pages(), str(target), image_fetcher=lambda src: b"not an image")
    assert len(Document(str(target)).inline_shapes) == 0
    assert "could not be added" in capsys.readouterr().out


def test_fetch_image_bytes_decodes_data_urls(sd):
    png = make_png()
    import base64

    data_url = "data:image/png;base64," + base64.b64encode(png).decode()
    assert sd.fetch_image_bytes(data_url) == png


def test_empty_document_still_writes_files(sd, tmp_path):
    sd.write_text_file([], str(tmp_path / "empty.txt"))
    sd.write_docx_file([], str(tmp_path / "empty.docx"))
    assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == "\n"
    assert Document(str(tmp_path / "empty.docx")) is not None
