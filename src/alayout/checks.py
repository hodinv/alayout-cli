from __future__ import annotations

from dataclasses import dataclass

from alayout.format import dp
from alayout.model import Rect, Snapshot, ViewNode
from alayout.parse.uiautomator import parse_bounds

MAX_DEPTH = 10
MIN_TOUCH_DP = 48


@dataclass(eq=False)
class Issue:
    check: str
    severity: str  # "warning" | "info"
    node: ViewNode
    message: str


def is_clickable(node: ViewNode) -> bool:
    if node.props.get("uiautomator", {}).get("clickable") == "true":
        return True
    flags = node.props.get("dumpsys", {}).get("flags", "")
    return len(flags) > 6 and flags[6] == "C"


def _has_area(r: Rect | None) -> bool:
    return r is not None and r.width > 0 and r.height > 0


def _overlap(a: Rect, b: Rect) -> bool:
    return a.left < b.right and b.left < a.right and a.top < b.bottom and b.top < a.bottom


def _has_text(node: ViewNode) -> bool:
    if node.visibility != "visible":
        return False
    return bool(node.text or node.props.get("uiautomator", {}).get("content-desc")) or any(
        _has_text(c) for c in node.children)


def _clipped(b: Rect, window: Rect | None, screen: Rect) -> bool:
    """True when b touches an edge where uiautomator clipped the window short of the screen
    (e.g. above the navigation bar of an edge-to-edge app): its real size is unknown."""
    if window is None:
        return False
    return ((b.bottom >= window.bottom and window.bottom < screen.bottom)
            or (b.right >= window.right and window.right < screen.right)
            or (b.top <= window.top and window.top > screen.top)
            or (b.left <= window.left and window.left > screen.left))


def _inside(inner: Rect, outer: Rect) -> bool:
    return (outer.left <= inner.left and outer.top <= inner.top
            and inner.right <= outer.right and inner.bottom <= outer.bottom)


def _name(node: ViewNode) -> str:
    return node.short_class + (f"#{node.id}" if node.id else "")


def run_checks(snap: Snapshot, max_depth: int = MAX_DEPTH) -> list[Issue]:
    issues: list[Issue] = []
    screen = Rect(0, 0, snap.screen[0], snap.screen[1])
    visible: list[tuple[ViewNode, tuple[ViewNode, ...]]] = []

    def walk(node: ViewNode, depth: int, ancestors: tuple[ViewNode, ...], parent_visible: bool) -> None:
        if not parent_visible or node.visibility == "gone":
            return
        if node.visibility == "invisible":
            if _has_area(node.bounds):
                issues.append(Issue("invisible-space", "info", node,
                                    f"INVISIBLE but still takes {node.bounds.width}x{node.bounds.height}px; "
                                    "use GONE if it should not reserve space"))
            return
        visible.append((node, ancestors))
        if depth == max_depth:
            deep = [c for c in node.children if c.visibility != "gone"]
            if deep:
                count = f"{len(deep)} views are" if len(deep) > 1 else "is"
                issues.append(Issue("deep-nesting", "info", deep[0],
                                    f"{count} nested {depth + 1} levels deep (more than {max_depth}); "
                                    "consider flattening"))
        for child in node.children:
            walk(child, depth + 1, ancestors + (node,), True)

    walk(snap.root, 0, (), True)
    window = parse_bounds(snap.root.props.get("uiautomator", {}).get("bounds", ""))

    off_screen: set[ViewNode] = set()
    for node, ancestors in visible:
        b = node.bounds
        if b is None or any(a in off_screen for a in ancestors):
            continue
        if not _has_area(b):
            if not node.children:
                issues.append(Issue("zero-size", "info", node, "visible but has zero size"))
            continue
        if not _overlap(b, screen):
            off_screen.add(node)
            issues.append(Issue("off-screen", "warning", node, f"entirely outside the screen {screen}"))
            continue
        clickable = is_clickable(node)
        maybe_clipped = node.sources == ["uiautomator"] and _clipped(b, window, screen)
        if clickable and snap.density and not maybe_clipped:
            w, h = dp(b.width, snap.density), dp(b.height, snap.density)
            if w < MIN_TOUCH_DP or h < MIN_TOUCH_DP:
                issues.append(Issue("touch-target", "warning", node,
                                    f"touch target {w}x{h}dp is smaller than {MIN_TOUCH_DP}x{MIN_TOUCH_DP}dp"))
        ui = node.props.get("uiautomator")
        if ui is not None and (clickable or "Image" in node.short_class) and not _has_text(node):
            parent = ancestors[-1] if ancestors else None
            siblings = parent.children if parent is not None else []
            labelled = (parent is not None and parent.bounds == b and _has_text(parent)) or any(
                s is not node and not is_clickable(s) and s.bounds is not None and _inside(s.bounds, b)
                and _has_text(s) for s in siblings)
            owner = next((a for a in reversed(ancestors) if is_clickable(a)), None)
            if labelled:
                pass
            elif owner is not None and _has_text(owner):
                if clickable:
                    issues.append(Issue("nested-clickable", "info", node,
                                        f"separately clickable inside labelled {_name(owner)}; screen "
                                        "readers announce it without a label (Compose: RadioButton/Checkbox "
                                        "onClick = null and Modifier.selectable/toggleable on the row)"))
            else:
                issues.append(Issue("missing-label", "warning", node,
                                    "clickable or image view without text or content description"))

    clickables = [(n, a) for n, a in visible
                  if is_clickable(n) and _has_area(n.bounds) and _overlap(n.bounds, screen)]
    for i, (a, a_anc) in enumerate(clickables):
        for b, b_anc in clickables[i + 1:]:
            if a in b_anc or b in a_anc:
                continue
            if _overlap(a.bounds, b.bounds):
                issues.append(Issue("overlapping-clickables", "warning", b,
                                    f"overlaps clickable {_name(a)} at {a.bounds}"))
    return issues
