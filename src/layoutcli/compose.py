from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from layoutcli.checks import is_clickable
from layoutcli.model import ViewNode

TOGGLE_CLASSES = ("CheckBox", "Switch", "RadioButton", "ToggleButton")


@dataclass(frozen=True)
class Component:
    """What a Compose semantics node most likely is. Inferred from semantics: composable names are not available."""

    kind: str
    label: str | None = None
    state: tuple[str, ...] = ()
    repeat: tuple[int, int] | None = None  # (index, count) among structurally identical siblings

    def describe(self) -> str:
        text = self.kind
        if self.label:
            text += f' "{self.label}"'
        if self.state:
            text += " [" + ", ".join(self.state) + "]"
        if self.repeat:
            text += f" ({self.repeat[0]} of {self.repeat[1]} similar)"
        return text


def _ui(node: ViewNode) -> dict[str, str]:
    return node.props.get("uiautomator", {})


def _text_label(node: ViewNode) -> str:
    texts: list[str] = []

    def visit(n: ViewNode) -> None:
        if n.visibility != "visible":
            return
        if n.text:
            texts.append(n.text.strip())
        for child in n.children:
            visit(child)

    visit(node)
    return " / ".join(t for t in texts if t)


def _state(ui: dict[str, str]) -> tuple[str, ...]:
    state = []
    if ui.get("checkable") == "true":
        state.append("checked" if ui.get("checked") == "true" else "unchecked")
    if ui.get("selected") == "true":
        state.append("selected")
    if ui.get("enabled") == "false":
        state.append("disabled")
    return tuple(state)


def _classify(node: ViewNode, ancestors: tuple[ViewNode, ...]) -> Component | None:
    ui = _ui(node)
    cls = node.short_class
    clickable = is_clickable(node)
    desc = ui.get("content-desc") or None
    label = _text_label(node) or desc
    state = _state(ui)
    parent = ancestors[-1] if ancestors else None

    if cls == "EditText":
        return Component("TextField", node.text or desc, state)
    if cls in TOGGLE_CLASSES:
        return Component(cls, label, state)
    if ui.get("checkable") == "true":
        return Component("Toggle", label, state)
    if cls == "Button":
        if parent is not None and is_clickable(parent) and parent.bounds == node.bounds:
            return None  # role marker of the clickable parent, which is reported as the Button
        return Component("Button", label, state)
    if clickable and any(c.short_class == "Button" and c.bounds == node.bounds for c in node.children):
        return Component("Button", label, state)
    if clickable:
        text = _text_label(node)
        if text:
            return Component("Clickable", text, state)
        if desc:
            return Component("IconButton", desc, state)
        owner = next((a for a in reversed(ancestors) if is_clickable(a) and _text_label(a)), None)
        return Component("Selector" if owner is not None else "Clickable", None, state)
    if ui.get("scrollable") == "true":
        return Component("Scrollable", None, state)
    return None


def _shape(node: ViewNode) -> tuple:
    return (node.short_class, tuple(_shape(c) for c in node.children))


def _region(root: ViewNode) -> list[tuple[ViewNode, tuple[ViewNode, ...]]]:
    """Semantics nodes inside Compose hosts (ComposeView / AndroidComposeView), with their ancestors."""
    found: list[tuple[ViewNode, tuple[ViewNode, ...]]] = []

    def walk(node: ViewNode, ancestors: tuple[ViewNode, ...], in_compose: bool) -> None:
        name = node.class_name
        if name.endswith("AndroidViewsHandler"):
            return  # interop Views hosted by Compose are regular Views
        is_host = name.endswith("ComposeView")
        if in_compose and not is_host and "uiautomator" in node.props:
            found.append((node, ancestors))
        for child in node.children:
            walk(child, ancestors + (node,), in_compose or is_host)

    walk(root, (), False)
    return found


def compose_nodes(root: ViewNode) -> set[ViewNode]:
    return {node for node, _ in _region(root)}


def infer_components(root: ViewNode) -> dict[ViewNode, Component]:
    """Component guesses for the semantics nodes inside Compose hosts."""
    found: dict[ViewNode, Component] = {}
    parents: dict[ViewNode, ViewNode] = {}
    for node, ancestors in _region(root):
        component = _classify(node, ancestors)
        if component is not None:
            found[node] = component
            if ancestors:
                parents[node] = ancestors[-1]

    groups: dict[tuple, list[ViewNode]] = defaultdict(list)
    for node, component in found.items():
        if node in parents:
            groups[(id(parents[node]), component.kind, _shape(node))].append(node)
    for members in groups.values():
        if len(members) > 1:
            for index, node in enumerate(members, start=1):
                c = found[node]
                found[node] = Component(c.kind, c.label, c.state, (index, len(members)))
    return found
