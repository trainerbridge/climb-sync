"""Programmatic icon generation for the system tray.

A two-peak mountain silhouette filled with the status color (green/yellow/red),
sized to nearly fill the tray cell so it stays readable at 16x16. Generated at
startup via Pillow - no static asset files for the tray icons. The .exe still
ships ONE static `assets/app.ico` as the Windows window icon (consumed at build
time by pyinstaller --icon=).
"""
from __future__ import annotations

from PIL import Image, ImageDraw

_COLOR_MAP: dict[str, tuple[int, int, int]] = {
    "green": (40, 180, 70),
    "yellow": (235, 200, 30),
    "red": (220, 50, 50),
}

_OUTLINE: tuple[int, int, int] = (30, 30, 30)
_SNOW: tuple[int, int, int] = (255, 255, 255)


def generate_icon(color: str, size: int = 64) -> Image.Image:
    """Mountain-shaped icon filled with the status color.

    pystray on Windows accepts a PIL.Image directly. Windows resamples down to
    16x16 for the tray. The mountain fills most of the canvas with a dark
    outline (readable on light themes) and a white snow cap (readable on dark
    themes).

    Raises KeyError on unknown color name (only "green"/"yellow"/"red" valid).
    """
    fill = _COLOR_MAP[color]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dc = ImageDraw.Draw(img)

    def p(fx: float, fy: float) -> tuple[int, int]:
        return (round(fx * size), round(fy * size))

    # Two-peak mountain filling most of the canvas.
    mountain = [
        p(0.02, 0.92),  # base left
        p(0.38, 0.08),  # main peak
        p(0.55, 0.45),  # valley
        p(0.72, 0.25),  # secondary peak
        p(0.98, 0.92),  # base right
    ]
    outline_w = max(2, size // 16)
    dc.polygon(mountain, fill=fill, outline=_OUTLINE, width=outline_w)

    # Snow cap on the main peak - small white triangle for dark-theme contrast.
    snow = [p(0.38, 0.08), p(0.28, 0.30), p(0.48, 0.30)]
    dc.polygon(snow, fill=_SNOW, outline=_OUTLINE, width=outline_w)

    return img
