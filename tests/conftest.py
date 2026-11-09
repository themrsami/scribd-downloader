import importlib.util
import pathlib
import struct
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scribd-downloader.py"


def load_module():
    # The script name contains a hyphen, so import it by path.
    spec = importlib.util.spec_from_file_location("scribd_downloader", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def sd():
    return load_module()


def make_png(width=4, height=2, rgb=(255, 0, 0)):
    """Build a tiny valid PNG without any image library."""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def run(text, x, y, size=16.0, w=None, font="Arial", color="rgb(0, 0, 0)", bold=False, italic=False):
    """Shorthand for one extracted text run (same shape the browser returns)."""
    return {
        "text": text,
        "x": x,
        "y": y,
        "w": w if w is not None else len(text) * size * 0.5,
        "size": size,
        "font": font,
        "color": color,
        "bold": bold,
        "italic": italic,
    }
