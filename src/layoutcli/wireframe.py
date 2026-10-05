from __future__ import annotations

import math
from typing import Iterator

from rich.text import Text

from layoutcli.model import Rect, ViewNode

FRAME_STYLE = "grey50"
SELECTED_STYLE = "bold yellow"
_EPS = 1e-9


def visible_nodes(root: ViewNode) -> Iterator[ViewNode]:
    if root.visibility != "visible":
        return
    yield root
    for child in root.children:
        yield from visible_nodes(child)


class _Canvas:
    def __init__(self, cols: int, rows: int, sx: float, sy: float):
        self.cols, self.rows, self.sx, self.sy = cols, rows, sx, sy
        self.chars = [[" "] * cols for _ in range(rows)]
        self.styles: list[list[str]] = [[""] * cols for _ in range(rows)]

    def put(self, x: int, y: int, ch: str, style: str) -> None:
        if 0 <= x < self.cols and 0 <= y < self.rows:
            self.chars[y][x] = ch
            self.styles[y][x] = style

    def box(self, r: Rect, style: str, label: str | None = None) -> None:
        x0 = math.floor(r.left * self.sx + _EPS)
        y0 = math.floor(r.top * self.sy + _EPS)
        x1 = max(x0, math.ceil(r.right * self.sx - _EPS) - 1)
        y1 = max(y0, math.ceil(r.bottom * self.sy - _EPS) - 1)
        for x in range(max(x0, 0), min(x1, self.cols - 1) + 1):
            self.put(x, y0, "─", style)
            self.put(x, y1, "─", style)
        for y in range(max(y0, 0), min(y1, self.rows - 1) + 1):
            self.put(x0, y, "│", style)
            self.put(x1, y, "│", style)
        if x1 > x0 and y1 > y0:
            self.put(x0, y0, "┌", style)
            self.put(x1, y0, "┐", style)
            self.put(x0, y1, "└", style)
            self.put(x1, y1, "┘", style)
        if label and x1 - x0 > 1:
            for i, ch in enumerate(label[: x1 - x0 - 1]):
                self.put(x0 + 1 + i, y0, ch, style)

    def to_text(self) -> Text:
        text = Text()
        for y in range(self.rows):
            if y:
                text.append("\n")
            chars, styles = self.chars[y], self.styles[y]
            start = 0
            for x in range(1, self.cols + 1):
                if x == self.cols or styles[x] != styles[start]:
                    text.append("".join(chars[start:x]), style=styles[start])
                    start = x
        return text


def render_wireframe(root: ViewNode, screen: tuple[int, int], cols: int, rows: int,
                     selected: ViewNode | None = None, label: str | None = None) -> Text:
    sw, sh = screen
    if cols <= 0 or rows <= 0 or sw <= 0 or sh <= 0:
        return Text("")
    scale = min(cols / sw, 2 * rows / sh)
    canvas = _Canvas(cols, rows, scale, scale / 2)
    for node in visible_nodes(root):
        if node is not selected and node.bounds is not None:
            canvas.box(node.bounds, FRAME_STYLE)
    if selected is not None and selected.bounds is not None:
        canvas.box(selected.bounds, SELECTED_STYLE, label=label or selected.short_class)
    return canvas.to_text()
