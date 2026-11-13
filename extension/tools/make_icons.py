"""Draws the extension icon (an original mark: a cream page with a download arrow).

Usage: python extension/tools/make_icons.py
Requires Pillow. Writes extension/icons/icon-{16,32,48,128}.png.
"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "icons"
SCALE = 8  # supersampling for smooth edges


def draw(size):
    s = size * SCALE
    # Black tile with nearly square corners (palette: cream, black, grey).
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.09), fill=(20, 20, 20, 255))

    # White page with a folded corner.
    left, right, page_top, page_bottom = s * 0.25, s * 0.75, s * 0.16, s * 0.84
    fold = s * 0.17
    d.polygon(
        [(left, page_top), (right - fold, page_top), (right, page_top + fold), (right, page_bottom), (left, page_bottom)],
        fill=(251, 248, 241, 255),
    )
    d.polygon(
        [(right - fold, page_top), (right - fold, page_top + fold), (right, page_top + fold)],
        fill=(207, 201, 187, 255),
    )

    # Download arrow.
    ink = (20, 20, 20, 255)
    cx = s * 0.5
    stem = s * 0.052
    d.rounded_rectangle((cx - stem, s * 0.36, cx + stem, s * 0.60), radius=stem, fill=ink)
    head = s * 0.135
    d.polygon([(cx - head, s * 0.52), (cx + head, s * 0.52), (cx, s * 0.67)], fill=ink)
    d.rounded_rectangle((cx - s * 0.15, s * 0.715, cx + s * 0.15, s * 0.715 + stem * 1.7), radius=stem, fill=ink)

    return icon.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for size in (16, 32, 48, 128):
        draw(size).save(OUT / f"icon-{size}.png")
        print("wrote", f"icon-{size}.png")
