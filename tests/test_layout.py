"""Issue #6: rebuilding lines and paragraphs from Scribd's positioned HTML text."""

import pytest

from conftest import run


def texts(lines):
    return ["".join(segment["text"] for segment in line["segments"]) for line in lines]


def test_runs_on_the_same_line_are_joined_left_to_right(sd):
    runs = [run("world", 70, 100.4), run("Hello ", 10, 100)]
    assert texts(sd.build_lines(runs)) == ["Hello world"]


def test_space_added_for_visual_gap_but_not_before_punctuation(sd):
    runs = [
        run("It", 10, 100, w=12),
        run("’s", 23, 100, w=10),  # touching: no space
        run("fine", 60, 100, w=30),  # gap: space
        run(".", 95, 100, w=4),  # gap but punctuation: no space
    ]
    assert texts(sd.build_lines(runs)) == ["It’s fine."]


def test_nbsp_becomes_space_and_ends_are_trimmed(sd):
    runs = [run(" Total: ", 10, 100), run("42 ", 90, 100)]
    assert texts(sd.build_lines(runs)) == ["Total: 42"]


def test_whitespace_only_lines_are_dropped(sd):
    assert sd.build_lines([run(" ", 10, 100), run("   ", 10, 200)]) == []


def test_small_bullet_glyph_stays_on_its_line(sd):
    # Scribd draws bullets in a tiny font, so their top sits a bit lower.
    runs = [
        run(" ", 97, 585.5, size=18, w=5),
        run("First item", 118, 591.0, size=16.6),
        run("•", 88.8, 594.1, size=6.65, w=4),
        run("Second line", 113, 612.8, size=16.6),
    ]
    assert texts(sd.build_lines(runs)) == ["• First item", "Second line"]


def test_wide_gap_becomes_tab_stop(sd):
    runs = [run("HARVARD UNIVERSITY", 60, 100, w=600), run("Cambridge, MA", 820, 100)]
    (line,) = sd.build_lines(runs)
    assert texts([line]) == ["HARVARD UNIVERSITY\tCambridge, MA"]
    assert line["tab_stops"] == [820]


def test_wrapped_lines_merge_into_one_paragraph(sd):
    lines = sd.build_lines(
        [
            run("This is a long sentence that wraps onto", 50, 100, w=800),
            run("the next line of the page.", 50, 120, w=300),
            run("New paragraph after a gap.", 50, 170, w=300),
        ]
    )
    paragraphs = sd.build_paragraphs(lines, page_width=900)
    assert [sd.paragraph_text(p) for p in paragraphs] == [
        "This is a long sentence that wraps onto the next line of the page.",
        "New paragraph after a gap.",
    ]
    assert paragraphs[0]["line_count"] == 2


def test_hyphenated_line_end_is_joined_without_space(sd):
    lines = sd.build_lines(
        [run("A well-known user-", 50, 100, w=800), run("friendly tool.", 50, 120, w=200)]
    )
    (paragraph,) = sd.build_paragraphs(lines, page_width=900)
    assert sd.paragraph_text(paragraph) == "A well-known user-friendly tool."


def test_short_line_ends_paragraph(sd):
    lines = sd.build_lines(
        [
            run("A full width line of text that goes all the way", 50, 100, w=800),
            run("Short end.", 50, 120, w=100),
            run("Next paragraph without extra spacing.", 50, 140, w=800),
        ]
    )
    assert len(sd.build_paragraphs(lines, page_width=900)) == 2


def test_list_items_split_and_hanging_lines_join(sd):
    lines = sd.build_lines(
        [
            run("1. Professional Design: a clean layout that", 50, 100, w=800),
            run("looks polished.", 70, 120, w=200),
            run("2. Strategic Sections: contact, summary.", 50, 140, w=800),
            run("• bullet item", 50, 160, w=800),
        ]
    )
    assert [sd.paragraph_text(p) for p in sd.build_paragraphs(lines, page_width=900)] == [
        "1. Professional Design: a clean layout that looks polished.",
        "2. Strategic Sections: contact, summary.",
        "• bullet item",
    ]


def test_bold_lead_in_starts_new_paragraph(sd):
    lines = sd.build_lines(
        [
            run("Concise: Hiring managers have limited time. A", 50, 100, w=800),
            run("resume should be short to read in full.", 50, 120, w=800),
            run("Reader-Friendly:", 50, 140, w=150, bold=True),
            run(" A shorter resume is read entirely.", 200, 140, w=600),
        ]
    )
    paragraphs = sd.build_paragraphs(lines, page_width=900)
    assert len(paragraphs) == 2
    assert sd.paragraph_text(paragraphs[1]).startswith("Reader-Friendly:")


def test_different_font_size_starts_new_paragraph(sd):
    lines = sd.build_lines(
        [run("Big Heading", 50, 100, size=32, w=800), run("Body text", 50, 140, size=16, w=800)]
    )
    assert len(sd.build_paragraphs(lines, page_width=900)) == 2


def test_centered_paragraph_detected(sd):
    lines = sd.build_lines([run("Title", 400, 100, w=100)])
    (paragraph,) = sd.build_paragraphs(lines, page_width=900)
    assert paragraph["centered"] is True
    lines = sd.build_lines([run("Left", 50, 100, w=100)])
    (paragraph,) = sd.build_paragraphs(lines, page_width=900)
    assert paragraph["centered"] is False


def test_full_width_lines_and_list_items_are_not_centered(sd):
    # Indented, nearly full-width wrapped list item (centre close to page centre).
    lines = sd.build_lines(
        [
            run("3. Customizable Fonts and Colors: personalize your resume by", 160, 100, w=640),
            run("fonts and colors, allowing you to match your style.", 183, 124, w=580),
        ]
    )
    (paragraph,) = sd.build_paragraphs(lines, page_width=1000)
    assert paragraph["centered"] is False

    lines = sd.build_lines([run("A body line spanning most of the page width here", 150, 100, w=720)])
    (paragraph,) = sd.build_paragraphs(lines, page_width=1000)
    assert paragraph["centered"] is False


def test_tiny_images_left_of_text_become_bullets(sd):
    # Scribd draws these bullets as sprite images: one dot, and one narrow
    # strip covering the next two items.
    page = {
        "pageNum": 1,
        "width": 1000,
        "runs": [
            run("Concise: short and clear.", 170, 322, size=18, w=400),
            run("Readable: easy to scan.", 170, 394, size=18, w=400),
            run("Custom: easy to change.", 170, 466, size=18, w=400),
            run("Plain paragraph far from the bullets.", 50, 600, size=18, w=600),
        ],
        "images": [
            {"src": "dot.jpg", "x": 155, "y": 328, "w": 7.8, "h": 7.8},
            {"src": "dot.jpg", "x": 155, "y": 400, "w": 7.8, "h": 78.7},
            {"src": "photo.jpg", "x": 50, "y": 700, "w": 400, "h": 300},
        ],
    }
    blocks = sd.layout_page(page)
    texts_by_type = [
        (block["type"], sd.paragraph_text(block) if block["type"] == "paragraph" else block["src"])
        for block in blocks
    ]
    assert texts_by_type == [
        ("paragraph", "• Concise: short and clear."),
        ("paragraph", "• Readable: easy to scan."),
        ("paragraph", "• Custom: easy to change."),
        ("paragraph", "Plain paragraph far from the bullets."),
        ("image", "photo.jpg"),
    ]


def test_styles_are_kept_per_segment(sd):
    (line,) = sd.build_lines(
        [
            run("Name:", 50, 100, w=60, bold=True, color="rgb(255, 0, 0)", font="Times New Roman"),
            run(" Jane", 112, 100, w=50),
        ]
    )
    first, second = line["segments"]
    assert first["text"] == "Name:"
    assert first["style"] == ("Times New Roman", 16.0, "FF0000", True, False, 0.0)
    assert second["style"] == ("Arial", 16.0, "000000", False, False, 0.0)


def test_layout_page_orders_text_and_images_top_to_bottom(sd):
    page = {
        "pageNum": 1,
        "width": 900,
        "height": 1200,
        "runs": [run("Below image", 50, 400, w=800), run("Above image", 50, 50, w=800)],
        "images": [{"src": "img.png", "x": 50, "y": 100, "w": 300, "h": 200}],
    }
    assert [block["type"] for block in sd.layout_page(page)] == ["paragraph", "image", "paragraph"]


def test_render_text_has_page_markers(sd):
    pages = [
        {"pageNum": 1, "width": 900, "runs": [run("Page one text.", 50, 50, w=800)], "images": []},
        {"pageNum": 2, "width": 900, "runs": [], "images": []},
    ]
    assert sd.render_text(pages) == (
        "===== Page 1 =====\n\nPage one text.\n\n===== Page 2 =====\n"
    )


@pytest.mark.parametrize(
    "value, expected",
    [
        ("rgb(163, 163, 163)", "A3A3A3"),
        ("rgba(0, 128, 255, 0.5)", "0080FF"),
        ("#abc", "AABBCC"),
        ("#A0B1C2", "A0B1C2"),
        ("transparent", None),
        ("", None),
    ],
)
def test_css_color_to_hex(sd, value, expected):
    assert sd.css_color_to_hex(value) == expected


def test_estimated_width_caps_inflated_browser_widths(sd):
    assert sd.estimated_width({"text": "If ", "size": 10, "w": 137}) == pytest.approx(12)
    assert sd.estimated_width({"text": "Wide", "size": 10, "w": 20}) == 20
    assert sd.estimated_width({"text": "abc", "size": 10, "w": 0}) == pytest.approx(18)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("• item", True),
        ("- item", True),
        ("1. item", True),
        ("12) item", True),
        ("a) item", True),
        ("(iv) item", True),
        ("Item", False),
        ("1.5 million", False),
        ("-5 degrees", False),
    ],
)
def test_list_marker_detection(sd, text, expected):
    assert sd.starts_with_list_marker(text) is expected
