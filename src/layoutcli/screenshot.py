from __future__ import annotations

import math

from PIL import Image, ImageDraw
from rich.color import Color
from rich.style import Style
from rich.text import Text

from layoutcli.model import ViewNode

HIGHLIGHT = (255, 215, 0)
DIM = 0.45  # brightness kept outside the selected view in annotate()


def fit_image(image: Image.Image, cols: int, rows: int) -> Image.Image | None:
    """The image scaled to fit cols x rows half-block cells (two pixels per cell vertically)."""
    iw, ih = image.size
    if cols <= 0 or rows <= 0 or iw <= 0 or ih <= 0:
        return None
    scale = min(cols / iw, 2 * rows / ih)
    w = max(1, min(cols, round(iw * scale)))
    h = max(2, min(2 * rows, round(ih * scale)))
    h -= h % 2
    return image.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)


def _box(selected: ViewNode | None, screen: tuple[int, int], size: tuple[int, int]) -> list[int] | None:
    if selected is None or selected.bounds is None or screen[0] <= 0 or screen[1] <= 0:
        return None
    sx, sy = size[0] / screen[0], size[1] / screen[1]
    b = selected.bounds
    x0, y0 = math.floor(b.left * sx), math.floor(b.top * sy)
    return [x0, y0, max(x0, math.ceil(b.right * sx) - 1), max(y0, math.ceil(b.bottom * sy) - 1)]


def render_screenshot(image: Image.Image, screen: tuple[int, int], cols: int, rows: int,
                      selected: ViewNode | None = None, fitted: Image.Image | None = None) -> Text:
    """Half-block rendering; pass `fitted` (from fit_image) to skip resizing on every redraw."""
    small = fitted if fitted is not None else fit_image(image, cols, rows)
    if small is None:
        return Text("")
    small = small.copy()
    w, h = small.size
    box = _box(selected, screen, small.size)
    if box is not None:
        ImageDraw.Draw(small).rectangle(box, outline=HIGHLIGHT)
    px = small.load()
    text = Text()
    for y in range(0, h - 1, 2):
        if y:
            text.append("\n")
        for x in range(w):
            top, bottom = px[x, y], px[x, y + 1]
            text.append("▀", Style(color=Color.from_rgb(*top), bgcolor=Color.from_rgb(*bottom)))
    return text


def annotate(image: Image.Image, screen: tuple[int, int], selected: ViewNode | None) -> Image.Image:
    """Full-resolution copy with the selected view outlined and everything else dimmed."""
    out = image.convert("RGB")
    box = _box(selected, screen, out.size)
    if box is None:
        return out.copy()
    dimmed = out.point(lambda v: int(v * DIM))
    mask = Image.new("L", out.size, 255)
    ImageDraw.Draw(mask).rectangle(box, fill=0)
    result = Image.composite(dimmed, out, mask)
    width = max(3, round(min(out.size) / 270))
    x0, y0, x1, y1 = box
    ImageDraw.Draw(result).rectangle([x0 - width + 1, y0 - width + 1, x1 + width - 1, y1 + width - 1],
                                     outline=HIGHLIGHT, width=width)
    return result
