from __future__ import annotations

from rich.text import Text

from layoutcli.model import Rect, ViewNode


def dp(px: int, density: int | None) -> int | None:
    return round(px * 160 / density) if density else None


def size_text(rect: Rect, density: int | None) -> str:
    text = f"{rect.width}x{rect.height}px"
    if density:
        text += f" ({dp(rect.width, density)}x{dp(rect.height, density)}dp)"
    return text


def _clip(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def node_label(node: ViewNode) -> Text:
    label = Text(node.short_class, style="bold")
    if node.id:
        label.append(f" #{node.id}", style="cyan")
    if node.text:
        label.append(f' "{_clip(node.text, 30)}"', style="green")
    if node.bounds is not None:
        label.append(f" {node.bounds.width}x{node.bounds.height}", style="dim")
    if node.visibility != "visible":
        label.append(f" [{node.visibility}]", style="red")
        label.stylize("dim")
    if node.sources == ["uiautomator"]:
        label.append(" ◇", style="magenta")
    return label


def node_rows(node: ViewNode, density: int | None) -> list[tuple[str, str, str]]:
    rows = [("view", "class", node.class_name),
            ("view", "id", node.id or "—"),
            ("view", "visibility", node.visibility)]
    if node.bounds is not None:
        rows.append(("view", "bounds", str(node.bounds)))
        rows.append(("view", "size", size_text(node.bounds, density)))
    if node.text:
        rows.append(("view", "text", node.text))
    rows.append(("view", "sources", ", ".join(node.sources)))
    for source in sorted(node.props):
        for key, value in node.props[source].items():
            rows.append((source, key, value))
    return rows
