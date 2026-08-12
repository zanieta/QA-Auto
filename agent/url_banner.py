"""Stamp the page URL onto a screenshot.

Playwright's `page.screenshot()` captures the page viewport only — it cannot
include Chrome's real address bar — so the URL is drawn into a strip ABOVE the
page pixels. The page image itself is never modified or covered.

Pure and browser-free, so it unit-tests without Playwright. Any failure returns
the original bytes: a cosmetic banner must never fail a step.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

BANNER_HEIGHT = 32
_BG = (32, 33, 36)        # Chrome's dark toolbar
_FG = (232, 234, 237)
_PAD_X = 12
_FONT_SIZE = 14
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def stamp_url(png_bytes: bytes, url: str) -> bytes:
    """Return a new PNG with `url` drawn in a strip above the page pixels.

    Returns `png_bytes` unchanged if Pillow is missing, the input is not a
    decodable image, or drawing raises for any other reason.
    """
    try:
        from PIL import Image, ImageDraw

        src = Image.open(io.BytesIO(png_bytes))
        src.load()
        src = src.convert("RGB")

        out = Image.new("RGB", (src.width, src.height + BANNER_HEIGHT), _BG)
        out.paste(src, (0, BANNER_HEIGHT))

        draw = ImageDraw.Draw(out)
        font = _load_font()
        text = _fit(draw, font, url or "(no url)", src.width - 2 * _PAD_X)
        # Fixed y rather than anchor="lm": PIL's default bitmap font rejects
        # the anchor kwarg, which would send every capture down the fallback.
        draw.text((_PAD_X, (BANNER_HEIGHT - _FONT_SIZE) // 2), text, fill=_FG, font=font)

        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        log.debug("URL banner skipped; returning raw screenshot", exc_info=True)
        return png_bytes


def _load_font():
    """A real TrueType face if one is installed, else PIL's bitmap fallback."""
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, _FONT_SIZE)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit(draw, font, text: str, max_width: int) -> str:
    """Trim `text` with a trailing ellipsis until it fits `max_width` pixels."""
    if max_width <= 0:
        return ""

    def width_of(candidate: str) -> float:
        try:
            return draw.textlength(candidate, font=font)
        except Exception:
            return len(candidate) * _FONT_SIZE * 0.6

    if width_of(text) <= max_width:
        return text
    trimmed = text
    while trimmed and width_of(trimmed + "…") > max_width:
        trimmed = trimmed[:-1]
    return trimmed + "…" if trimmed else ""
