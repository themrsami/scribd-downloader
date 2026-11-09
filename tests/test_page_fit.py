"""
Keeping Word pages identical to Scribd pages: real footers, table-of-contents
dot leaders, condensed substitute fonts, floating side-by-side images and
rules, and Private Use Area bullets.
"""

import pytest
from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml.ns import qn

from conftest import make_png, run


def body_page(number, extra_runs=(), images=()):
    return {
        "pageNum": number,
        "width": 1000,
        "height": 1300,
        "runs": [
            run(f"Body text on page {number}.", 80, 100, w=300),
            *extra_runs,
            run("Course Manual", 80, 1200, w=150),
            run(f"Confidential 2018 Workday, Inc. {number}", 80, 1230, w=400),
        ],
        "images": list(images),
    }


# --- Footers -----------------------------------------------------------------


def test_repeating_bottom_lines_are_footers(sd):
    pages = [body_page(n) for n in range(1, 6)]
    blocks = [sd.layout_page(page) for page in pages]
    assert sd.find_repeating_footers(pages, blocks) == {
        "course manual",
        "confidential # workday, inc. #",
    }


def test_one_off_text_near_the_bottom_is_not_a_footer(sd):
    pages = [body_page(n) for n in range(1, 6)]
    pages[2]["runs"].append(run("A closing sentence that appears once.", 80, 1160, w=500))
    blocks = [sd.layout_page(page) for page in pages]
    footers = sd.find_repeating_footers(pages, blocks)
    assert "a closing sentence that appears once." not in footers


def test_short_documents_have_no_detected_footers(sd):
    pages = [body_page(1), body_page(2)]
    assert sd.find_repeating_footers(pages, [sd.layout_page(p) for p in pages]) == set()


def test_footers_go_into_the_word_footer(sd, tmp_path):
    pages = [body_page(n) for n in range(1, 4)]
    target = tmp_path / "footers.docx"
    sd.write_docx_file(pages, str(target), image_fetcher=lambda src: make_png())
    document = Document(str(target))

    body_text = [p.text for p in document.paragraphs if p.text]
    assert body_text == ["Body text on page 1.", "Body text on page 2.", "Body text on page 3."]
    for number, section in enumerate(document.sections, start=1):
        footer_text = [p.text for p in section.footer.paragraphs if p.text]
        assert footer_text == ["Course Manual", f"Confidential 2018 Workday, Inc. {number}"]
        assert section.footer.is_linked_to_previous is False
        # Body ends where the footer starts in the original (y=1200 of 1300).
        assert section.bottom_margin == pytest.approx(int((1300 - 1200) * 0.75 * 12700), abs=12700)


# --- Table of contents -------------------------------------------------------


def test_dot_leaders_become_a_dotted_right_tab(sd, tmp_path):
    page = {
        "pageNum": 1,
        "width": 1000,
        "height": 1300,
        "runs": [
            run("Objectives ......................................... 23", 80, 100, w=840),
            run("Schedule an Integration", 80, 130, w=250),
        ],
        "images": [],
    }
    target = tmp_path / "toc.docx"
    sd.write_docx_file([page], str(target))
    paragraph = Document(str(target)).paragraphs[0]
    assert paragraph.text == "Objectives\t23"
    (tab,) = paragraph.paragraph_format.tab_stops
    assert tab.alignment == WD_TAB_ALIGNMENT.RIGHT
    assert tab.leader == WD_TAB_LEADER.DOTS
    # TXT keeps the original dots.
    assert "Objectives ......................................... 23" in sd.render_text([page])


# --- Substitute font widths ---------------------------------------------------


def measured(text, x, y, vw, fw, sw=5.0, **kwargs):
    item = run(text, x, y, **kwargs)
    item.update({"vx": x, "vw": vw, "fw": fw, "sw": sw})
    return item


def test_line_width_includes_spaces_between_runs(sd):
    lines = sd.build_lines(
        [
            measured("Hello", 100, 100, vw=40, fw=50, w=40),
            measured("wide world", 150, 100, vw=80, fw=100, w=80),
        ]
    )
    (line,) = lines
    assert "".join(s["text"] for s in line["segments"]) == "Hello wide world"
    assert line["width_original"] == pytest.approx(130)  # 100 -> 230
    assert line["width_word"] == pytest.approx(50 + 100 + 5)  # glyphs + 1 inserted space


def test_wider_substitute_font_is_condensed_never_stretched(sd, tmp_path):
    wide = {
        "pageNum": 1,
        "width": 1000,
        "height": 1300,
        "runs": [measured("Text that Word draws wider", 100, 100, vw=270, fw=300, w=270)],
        "images": [],
    }
    narrow = {
        "pageNum": 1,
        "width": 1000,
        "height": 1300,
        "runs": [measured("Text that Word draws narrower", 100, 100, vw=300, fw=250, w=300)],
        "images": [],
    }
    for page, expected in ((wide, "90"), (narrow, None)):
        target = tmp_path / "scale.docx"
        sd.write_docx_file([page], str(target))
        run_properties = Document(str(target)).paragraphs[0].runs[0]._r.rPr
        scale = run_properties.find(qn("w:w"))
        assert (scale.get(qn("w:val")) if scale is not None else None) == expected


def test_run_properties_stay_in_schema_order(sd, tmp_path):
    # Word rejects w:spacing / w:w placed after w:sz.
    page = {
        "pageNum": 1,
        "width": 1000,
        "height": 1300,
        "runs": [{**measured("SPACED OUT HEADING", 100, 100, vw=270, fw=300, w=270), "spacing": 2.0}],
        "images": [],
    }
    target = tmp_path / "order.docx"
    sd.write_docx_file([page], str(target))
    tags = [child.tag.split("}")[1] for child in Document(str(target)).paragraphs[0].runs[0]._r.rPr]
    assert tags.index("spacing") < tags.index("sz")


# --- Floating images ----------------------------------------------------------


def test_side_by_side_images_and_thin_rules_float(sd):
    page = {
        "pageNum": 1,
        "width": 1000,
        "height": 1300,
        "runs": [run("Caption below the diagram", 100, 500, w=300)],
        "images": [
            {"src": "left.png", "x": 100, "y": 100, "w": 200, "h": 150},
            {"src": "right.png", "x": 400, "y": 120, "w": 200, "h": 150},
            {"src": "rule.png", "x": 80, "y": 600, "w": 800, "h": 2},
            {"src": "figure.png", "x": 100, "y": 700, "w": 400, "h": 300},
        ],
    }
    floating = {b["src"]: b["behind"] for b in sd.layout_page(page) if b["type"] == "image"}
    assert floating == {"left.png": True, "right.png": True, "rule.png": True, "figure.png": False}


def test_rule_under_text_is_anchored_to_that_paragraph(sd, tmp_path):
    page = {
        "pageNum": 1,
        "width": 1000,
        "height": 1300,
        "runs": [run("Firstname Lastname", 400, 242, size=16, w=160), run("Address", 400, 263, w=100)],
        "images": [{"src": "rule.png", "x": 60, "y": 260, "w": 880, "h": 1}],
    }
    target = tmp_path / "rule.docx"
    sd.write_docx_file([page], str(target), image_fetcher=lambda src: make_png())
    document = Document(str(target))
    (anchor,) = document.element.body.findall(".//" + qn("wp:anchor"))
    paragraph = anchor.getparent().getparent().getparent()
    assert "Firstname Lastname" in "".join(t.text for t in paragraph.iter(qn("w:t")))
    offset_px = int(anchor.find(qn("wp:positionV")).find(qn("wp:posOffset")).text) / 9525
    # 18px below the text top, plus a small nudge to clear descenders.
    assert 18 <= offset_px <= 18 + 0.15 * 16 + 0.01


# --- Private Use Area bullets -------------------------------------------------


def test_private_use_area_bullet_becomes_bullet(sd):
    lines = sd.build_lines(
        [run("", 170, 104, size=6, w=4, font="Arial"), run("Look at the structure", 199, 100, w=300)]
    )
    assert "".join(s["text"] for s in lines[0]["segments"]) == "• Look at the structure"
    (paragraph,) = sd.build_paragraphs(lines, page_width=1000)
    assert sd.starts_with_list_marker(sd.paragraph_text(paragraph))
