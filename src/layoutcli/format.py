from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from layoutcli.model import Rect, ViewNode

if TYPE_CHECKING:
    from layoutcli.compose import Component


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


def compose_kind(node: ViewNode, component: Component | None) -> tuple[str, str | None]:
    """How a Compose semantics node is shown: inferred kind and label instead of its accessibility class."""
    if component is not None:
        return component.kind, component.label
    cls = node.short_class
    if cls in ("TextView", "EditText"):
        return "Text", node.text
    if cls == "ImageView":
        return "Image", node.props.get("uiautomator", {}).get("content-desc") or None
    if cls == "Button":
        return "Button role", None
    return "Group", node.text


def _compose_label(node: ViewNode, warning: bool, component: Component | None) -> Text:
    kind, text = compose_kind(node, component)
    label = Text(kind, style="bold cyan")
    if text:
        label.append(f' "{_clip(text, 30)}"', style="green")
    if component is not None and component.state:
        label.append(" [" + ", ".join(component.state) + "]", style="yellow")
    if node.bounds is not None:
        label.append(f" {node.bounds.width}x{node.bounds.height}", style="dim")
    if component is not None and component.repeat:
        label.append(f" ({component.repeat[0]} of {component.repeat[1]} similar)", style="dim")
    if node.visibility != "visible":
        label.append(f" [{node.visibility}]", style="red")
        label.stylize("dim")
    if warning:
        label.append(" ⚠", style="yellow")
    return label


def node_label(node: ViewNode, warning: bool = False, component: Component | None = None,
               in_compose: bool = False) -> Text:
    if in_compose:
        return _compose_label(node, warning, component)
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
    if component is not None:
        label.append(f" ⟨{component.describe()}⟩", style="bold cyan")
    if warning:
        label.append(" ⚠", style="yellow")
    return label


def node_rows(node: ViewNode, density: int | None,
              component: Component | None = None) -> list[tuple[str, str, str]]:
    rows = [("view", "class", node.class_name),
            ("view", "id", node.id or "—"),
            ("view", "visibility", node.visibility)]
    if node.bounds is not None:
        rows.append(("view", "bounds", str(node.bounds)))
        rows.append(("view", "size", size_text(node.bounds, density)))
    if node.text:
        rows.append(("view", "text", node.text))
    rows.append(("view", "sources", ", ".join(node.sources)))
    if component is not None:
        rows.append(("compose", "component", component.kind))
        if component.label:
            rows.append(("compose", "label", component.label))
        if component.state:
            rows.append(("compose", "state", ", ".join(component.state)))
        if component.repeat:
            rows.append(("compose", "similar", f"{component.repeat[0]} of {component.repeat[1]}"))
    for source in sorted(node.props):
        for key, value in node.props[source].items():
            rows.append((source, key, value))
    return rows
