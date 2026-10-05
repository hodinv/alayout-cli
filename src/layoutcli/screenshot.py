from __future__ import annotations

import math

from PIL import Image, ImageDraw
from rich.color import Color
from rich.style import Style
from rich.text import Text

from layoutcli.model import ViewNode

HIGHLIGHT = (255, 215, 0)


def render_screenshot(image: Image.Image, screen: tuple[int, int], cols: int, rows: int,
                      selected: ViewNode | None = None) -> Text:
    iw, ih = image.size
    if cols <= 0 or rows <= 0 or iw <= 0 or ih <= 0:
        return Text("")
    scale = min(cols / iw, 2 * rows / ih)
    w = max(1, min(cols, round(iw * scale)))
    h = max(2, min(2 * rows, round(ih * scale)))
    h -= h % 2
    small = image.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
    if selected is not None and selected.bounds is not None and screen[0] > 0 and screen[1] > 0:
        sx, sy = w / screen[0], h / screen[1]
        b = selected.bounds
        x0, y0 = math.floor(b.left * sx), math.floor(b.top * sy)
        x1 = max(x0, math.ceil(b.right * sx) - 1)
        y1 = max(y0, math.ceil(b.bottom * sy) - 1)
        ImageDraw.Draw(small).rectangle([x0, y0, x1, y1], outline=HIGHLIGHT)
    px = small.load()
    text = Text()
    for y in range(0, h, 2):
        if y:
            text.append("\n")
        for x in range(w):
            top, bottom = px[x, y], px[x, y + 1]
            text.append("▀", Style(color=Color.from_rgb(*top), bgcolor=Color.from_rgb(*bottom)))
    return text
