"""Programmatic icon generation for the system tray.

D-03: three solid-color filled circles (green/yellow/red) with a dark outline.
Generated at startup via Pillow - no static asset files for the tray icons.
The .exe still ships ONE static `assets/app.ico` as the Windows window icon
(consumed at build time by pyinstaller --icon=).

Pattern 7 from 03-RESEARCH.md. Confidence: HIGH (pystray accepts PIL.Image
directly per its own usage docs).
"""
from __future__ import annotations

from PIL import Image, ImageDraw

# RGB triplets per D-03 - vivid + readable on both light and dark Windows themes
_COLOR_MAP: dict[str, tuple[int, int, int]] = {  # green/yellow/red only
    "green": (40, 180, 70),
    "yellow": (235, 200, 30),
    "red": (220, 50, 50),
}

# Outline color: dark gray reads cleanly against both light and dark backgrounds
_OUTLINE: tuple[int, int, int] = (40, 40, 40)


def generate_icon(color: str, size: int = 64) -> Image.Image:
    """Create an RGBA icon: filled circle of the given color, dark outline.

    pystray on Windows accepts a PIL.Image directly - no PNG/ICO conversion
    needed. Windows resamples down to 16x16 for the actual tray rendering.

    Raises KeyError on unknown color name (only "green"/"yellow"/"red" valid).
    """
    fill = _COLOR_MAP[color]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dc = ImageDraw.Draw(img)
    dc.ellipse(
        (4, 4, size - 5, size - 5),
        fill=fill,
        outline=_OUTLINE,
        width=4,
    )
    return img
