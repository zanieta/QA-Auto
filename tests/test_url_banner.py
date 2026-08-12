"""stamp_url tests — pure image work, no browser involved."""

from __future__ import annotations

import io

from agent.url_banner import BANNER_HEIGHT, stamp_url


def _png(width: int = 200, height: int = 100) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _size(png: bytes) -> tuple[int, int]:
    from PIL import Image

    return Image.open(io.BytesIO(png)).size


def test_stamp_url_grows_height_keeps_width():
    out = stamp_url(_png(200, 100), "https://test.souscheftech.com/account/recipes")
    assert _size(out) == (200, 100 + BANNER_HEIGHT)


def test_stamp_url_returns_a_valid_png():
    out = stamp_url(_png(), "https://example.com/x")
    assert out.startswith(b"\x89PNG")


def test_stamp_url_returns_input_unchanged_on_bad_bytes():
    junk = b"not-a-png-at-all"
    assert stamp_url(junk, "https://example.com") == junk


def test_stamp_url_tolerates_empty_url():
    out = stamp_url(_png(200, 100), "")
    assert _size(out) == (200, 100 + BANNER_HEIGHT)


def test_stamp_url_tolerates_a_very_long_url():
    long_url = "https://test.souscheftech.com/" + "segment/" * 60
    out = stamp_url(_png(200, 100), long_url)
    assert _size(out) == (200, 100 + BANNER_HEIGHT)
