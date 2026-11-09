"""
Layout fidelity: hidden text under images, fill-in blanks drawn as images,
boxes behind text, letter spacing, and Word pages matching Scribd pages.
"""

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Pt

from conftest import make_png, run


def layered(text, x, y, z, **kwargs):
    item = run(text, x, y, **kwargs)
    item["z"] = z
    return item


def image(src, x, y, w, h, z=3):
    return {"src": src, "x": x, "y": y, "w": w, "h": h, "z": z}


# --- Hidden text (edited PDFs) ---------------------------------------------


def test_text_under_opaque_jpeg_patch_is_dropped(sd):
    runs = [
        layered("City, State", 868, 548, z=2, w=73),  # old text, under the patch
        layered("City, State (or Remote)", 775, 546, z=4, w=165),  # new text, on top
        layered("ORGANIZATION", 60, 548, z=2, w=146),  # not covered
    ]
    patch = image("https://cdn.test/images/1-abc.jpg", 696, 545, 245, 22)
    kept = sd.remove_covered_runs(runs, [patch], 1000, 1300)
    assert [r["text"] for r in kept] == ["City, State (or Remote)", "ORGANIZATION"]


def test_png_patch_drops_text_only_when_newer_text_replaces_it(sd):
    patch = image("https://cdn.test/images/2-abc.png", 696, 563, 246, 21)
    replaced = [
        layered("City, State", 867, 565, z=2, w=73),
        layered("City, State (or Remote)", 776, 564, z=4, w=165),
    ]
    assert [r["text"] for r in sd.remove_covered_runs(replaced, [patch], 1000, 1300)] == [
        "City, State (or Remote)"
    ]

    # Text inside a transparent box (e.g. shaded callout) must be kept.
    inside_box = [layered("Callout text", 700, 565, z=2, w=100)]
    assert sd.remove_covered_runs(inside_box, [patch], 1000, 1300) == inside_box


def test_text_under_full_page_scan_is_kept(sd):
    # OCR text below a full-page scanned image is the only text there is.
    runs = [layered("Scanned words", 100, 100, z=2)]
    scan = image("https://cdn.test/images/1-scan.jpg", 0, 0, 1000, 1300)
    assert sd.remove_covered_runs(runs, [scan], 1000, 1300) == runs


def test_higher_layer_text_is_never_hidden_by_lower_image(sd):
    runs = [layered("Text above image", 700, 550, z=4, w=120)]
    patch = image("https://cdn.test/images/1-abc.jpg", 696, 545, 245, 22, z=3)
    assert sd.remove_covered_runs(runs, [patch], 1000, 1300) == runs


def test_is_opaque_image(sd):
    assert sd.is_opaque_image("https://cdn.test/images/1-a.jpg")
    assert sd.is_opaque_image("https://cdn.test/images/1-a.JPEG?x=1")
    assert sd.is_opaque_image("data:image/jpeg;base64,xx")
    assert not sd.is_opaque_image("https://cdn.test/images/1-a.png")
    assert not sd.is_opaque_image("data:image/png;base64,xx")


# --- Fill-in blanks ----------------------------------------------------------


def test_thin_image_between_words_becomes_underscores(sd):
    runs = [
        run("Study abroad coursework in ", 60, 418, size=16.6, w=200),
        run(". ", 311, 418, size=16.6, w=8),
    ]
    blank = image("blank.jpg", 260, 432, 50, 1.1)
    divider = image("rule.jpg", 58, 202, 882, 2.2)
    new_runs, remaining = sd.convert_line_images_to_text(runs, [blank, divider], 1000)

    assert remaining == [divider]  # long rule with no text beside it stays an image
    lines = sd.build_lines(new_runs)
    assert "".join(s["text"] for s in lines[0]["segments"]) == "Study abroad coursework in ______."


# --- Images behind text --------------------------------------------------------


def test_image_overlapping_text_is_marked_behind(sd):
    page = {
        "pageNum": 1,
        "width": 1000,
        "height": 1300,
        "runs": [run("Text inside a shaded box.", 200, 110, w=400), run("Body", 60, 600, w=60)],
        "images": [
            image("box.png", 150, 84, 700, 88),
            image("figure.png", 60, 300, 400, 200),
        ],
    }
    images = {b["src"]: b for b in sd.layout_page(page) if b["type"] == "image"}
    assert images["box.png"]["behind"] is True
    assert images["figure.png"]["behind"] is False


# --- Letter spacing ------------------------------------------------------------


def test_letter_spacing_kept_only_for_real_spaced_text(sd):
    heading = sd._run_style({**run("RESUMES AND COVER", 0, 0, size=13.7), "spacing": 1.55})
    kerning = sd._run_style({**run("Normal text", 0, 0, size=16), "spacing": -0.2})
    glyph = sd._run_style({**run("V", 0, 0, size=18.4), "spacing": 1.77})
    assert heading[5] == 1.6
    assert kerning[5] == 0.0
    assert glyph[5] == 0.0


# --- Word layout -----------------------------------------------------------------


def fidelity_pages():
    return [
        {
            "pageNum": 1,
            "width": 1000,
            "height": 1300,
            "runs": [
                {**run("SPACED HEADING", 400, 60, size=14, w=200, color="rgb(200, 0, 0)"), "spacing": 1.5},
                run("Text inside a shaded box that wraps", 200, 100, w=500),
                run("onto a second line in the box.", 200, 120, w=400),
                run("•", 80, 300, size=16, w=6),
                run("Bullet item that is long enough to wrap", 100, 300, w=840),
                run("and continues under the text.", 100, 320, w=400),
            ],
            "images": [image("box.png", 150, 84, 700, 88)],
        },
        {"pageNum": 2, "width": 1000, "height": 1300, "runs": [run("Page two", 60, 60, w=100)], "images": []},
    ]


def test_docx_layout_matches_scribd_pages(sd, tmp_path):
    target = tmp_path / "fidelity.docx"
    sd.write_docx_file(fidelity_pages(), str(target), image_fetcher=lambda src: make_png())
    document = Document(str(target))

    # One Word section per Scribd page, margins from the page's text area.
    assert len(document.sections) == 2
    first = document.sections[0]
    assert first.left_margin == Pt(80 * 0.75)
    assert first.top_margin == Pt(60 * 0.75)

    paragraphs = {p.text: p for p in document.paragraphs if p.text}

    # Exact line heights copied from the original (20px pitch -> 15pt).
    box = paragraphs["Text inside a shaded box that wraps onto a second line in the box."]
    assert box.paragraph_format.line_spacing_rule == WD_LINE_SPACING.EXACTLY
    assert box.paragraph_format.line_spacing == Pt(15)
    # Wraps where the original wrapped: right indent up to the box's edge.
    assert box.paragraph_format.right_indent is not None

    # Hanging indent: bullet at 80px, wrapped lines at 100px.
    bullet = paragraphs["• Bullet item that is long enough to wrap and continues under the text."]
    assert bullet.paragraph_format.left_indent == Pt(20 * 0.75)
    assert bullet.paragraph_format.first_line_indent == Pt(-20 * 0.75)

    # Letter-spaced heading -> Word character spacing (1.5px = 22.5 -> 22 twentieths of a pt).
    heading_run = paragraphs["SPACED HEADING"].runs[0]
    assert heading_run._r.rPr.find(qn("w:spacing")).get(qn("w:val")) in {"22", "23"}

    # The box floats behind the text: x from the page edge, y relative to the
    # paragraph just above it (the heading at y=60), so it moves with the text.
    anchors = document.element.body.findall(".//" + qn("wp:anchor"))
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.get("behindDoc") == "1"
    assert anchor.find(qn("wp:positionH")).get("relativeFrom") == "page"
    assert anchor.find(qn("wp:positionH")).find(qn("wp:posOffset")).text == str(150 * 9525)
    assert anchor.find(qn("wp:positionV")).get("relativeFrom") == "paragraph"
    assert anchor.find(qn("wp:positionV")).find(qn("wp:posOffset")).text == str((84 - 60) * 9525)
    anchored_in = anchor.getparent().getparent().getparent()  # drawing -> run -> paragraph
    assert "SPACED HEADING" in "".join(t.text for t in anchored_in.iter(qn("w:t")))
    assert len(document.inline_shapes) == 0
