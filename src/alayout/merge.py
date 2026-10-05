from __future__ import annotations

from collections import Counter
from dataclasses import replace

from alayout.model import Rect, ViewNode, short_id
from alayout.parse.dumpsys import DNode, DumpsysResult

COMPOSE_HOST_SUFFIX = "AndroidComposeView"
INTEROP_HANDLER_SUFFIX = "AndroidViewsHandler"


class MergeError(Exception):
    pass


def pick_window(roots: list[ViewNode], package: str | None) -> ViewNode | None:
    """The window belonging to `package`; any first window when the package is unknown."""
    if not roots:
        return None
    if package is None:
        return roots[0]
    for root in roots:
        if root.props.get("uiautomator", {}).get("package") == package:
            return root
    return None


def select_window(ui_roots: list[ViewNode], dump: DumpsysResult | None
                  ) -> tuple[ViewNode | None, str | None]:
    """Pick the uiautomator window that can be merged with the dumpsys activity.

    Returns (window, None) or (None, reason) when the active window is something else
    (another app, a system dialog, or a dialog of the same app).
    """
    if dump is None:
        return pick_window(ui_roots, None), None
    window = pick_window(ui_roots, dump.package)
    if window is None:
        found = ", ".join(sorted({r.props.get("uiautomator", {}).get("package", "?") for r in ui_roots}))
        return None, f"active window is {found or 'unknown'}, not {dump.package}; using dumpsys only"
    extent = _children_extent(dump.root)
    if window.bounds is not None and extent.width > 0 and not _covers_activity(window.bounds, extent):
        return None, (f"active window {window.bounds} does not match the activity "
                      f"({extent.width}x{extent.height}), probably a dialog; using dumpsys only")
    return window, None


MIN_WINDOW_COVERAGE = 0.75


def _covers_activity(window: Rect, extent: Rect) -> bool:
    """uiautomator clips the window to the visible area (e.g. above the navigation bar of an
    edge-to-edge app), so accept a window that fits inside the activity and covers most of it."""
    fits = window.width <= extent.width and window.height <= extent.height
    coverage = (window.width * window.height) / (extent.width * extent.height) if extent.height else 0
    return fits and coverage >= MIN_WINDOW_COVERAGE


def merge(dump: DumpsysResult | None, ui_roots: list[ViewNode]) -> ViewNode:
    ui, _ = select_window(ui_roots, dump)
    if dump is None:
        if ui is None:
            raise MergeError("no view hierarchy available")
        return ui
    origin = (ui.bounds.left, ui.bounds.top) if ui is not None and ui.bounds is not None else (0, 0)
    return _convert(dump.root, ui, origin)


def _compatible(d: DNode, u: ViewNode) -> bool:
    if d.visibility != "visible" or short_id(d.res_id) != u.id:
        return False
    if d.rel is None or u.bounds is None:
        return True
    return u.bounds.width <= d.rel.width and u.bounds.height <= d.rel.height


def _score(d: DNode, u: ViewNode, origin: tuple[int, int]) -> tuple[int, int]:
    """Lower is better: exact size match first, then distance of the top-left corners."""
    if d.rel is None or u.bounds is None:
        return (1, 0)
    expected = d.rel.offset(*origin)
    same_size = 0 if expected.size == u.bounds.size else 1
    return (same_size, abs(expected.left - u.bounds.left) + abs(expected.top - u.bounds.top))


def match_children(dkids: list[DNode], ukids: list[ViewNode], origin: tuple[int, int] = (0, 0)
                   ) -> tuple[list[tuple[DNode, ViewNode | None]], list[ViewNode]]:
    """Pair dumpsys children with uiautomator children.

    uiautomator orders children by screen position, dumpsys by child index, so pairs are
    chosen globally by score rather than by order.
    """
    candidates = sorted(
        (_score(d, u, origin), di, ui)
        for di, d in enumerate(dkids) for ui, u in enumerate(ukids) if _compatible(d, u))
    d_match: dict[int, int] = {}
    used: set[int] = set()
    for _, di, ui in candidates:
        if di not in d_match and ui not in used:
            d_match[di] = ui
            used.add(ui)
    pairs = [(d, ukids[d_match[di]] if di in d_match else None) for di, d in enumerate(dkids)]
    leftovers = [u for k, u in enumerate(ukids) if k not in used]
    return pairs, leftovers


def _children_extent(dn: DNode) -> Rect:
    rects = [c.rel for c in dn.children if c.rel is not None]
    if not rects:
        return Rect(0, 0, 0, 0)
    return Rect(0, 0, max(r.right for r in rects), max(r.bottom for r in rects))


def _dumpsys_props(dn: DNode) -> dict[str, str]:
    props = {"hash": dn.hash, "flags": dn.flags,
             "rel_bounds": str(dn.rel) if dn.rel else "", "resource_id": dn.res_id or ""}
    return {k: v for k, v in props.items() if v}


def _implied_origin(pairs: list[tuple[DNode, ViewNode | None]]) -> tuple[int, int] | None:
    """Where the children's coordinate origin really is, from children whose size uiautomator
    confirms (it knows scroll offsets, dumpsys does not)."""
    votes: Counter[tuple[int, int]] = Counter()
    for d, u in pairs:
        if u is not None and d.rel is not None and u.bounds is not None and u.bounds.size == d.rel.size:
            votes[(u.bounds.left - d.rel.left, u.bounds.top - d.rel.top)] += 1
    return votes.most_common(1)[0][0] if votes else None


def _axis(current: int, size: int, low: int, high: int, clip: tuple[int, int] | None) -> int:
    """Start coordinate of a view on one axis from its visible (clipped) extent low..high."""
    if high - low == size:
        return low
    if clip is None or high - low > size:
        return current
    if low > clip[0]:
        return low  # the start is visible, the end is clipped
    if high < clip[1]:
        return high - size  # the end is visible, the start is clipped
    return current


def _inside(inner: Rect, outer: Rect) -> bool:
    return (outer.left <= inner.left and outer.top <= inner.top
            and inner.right <= outer.right and inner.bottom <= outer.bottom)


def _locate(bounds: Rect, visible: Rect, implied: tuple[int, int] | None, clip: Rect | None) -> Rect:
    """Position of a view that uiautomator only reports clipped (partly scrolled out)."""
    w, h = bounds.width, bounds.height
    if implied is not None:
        candidate = Rect(implied[0], implied[1], implied[0] + w, implied[1] + h)
        if _inside(visible, candidate):
            return candidate
    left = _axis(bounds.left, w, visible.left, visible.right, (clip.left, clip.right) if clip else None)
    top = _axis(bounds.top, h, visible.top, visible.bottom, (clip.top, clip.bottom) if clip else None)
    return Rect(left, top, left + w, top + h)


def _interop_pins(host: DNode, origin: tuple[int, int], u_nodes: list[ViewNode]) -> dict[int, ViewNode]:
    """Views hosted by Compose (AndroidView) are in the dumpsys tree and also among the semantics
    nodes; pair them by screen bounds and id so they appear once."""
    found: list[tuple[DNode, Rect]] = []

    def collect(dn: DNode, at: tuple[int, int]) -> None:
        if dn.visibility != "visible" or dn.rel is None:
            return
        abs_rect = dn.rel.offset(*at)
        for child in dn.children:
            collect(child, (abs_rect.left, abs_rect.top))
        found.append((dn, abs_rect))  # post-order: the real view claims a match before its holder

    for child in host.children:
        if child.class_name.endswith(INTEROP_HANDLER_SUFFIX):
            collect(child, origin)
    candidates = [n for top in u_nodes for n, _ in top.walk()]
    pins: dict[int, ViewNode] = {}
    used: set[int] = set()
    for dn, abs_rect in found:
        match = next((u for u in candidates if id(u) not in used and u.bounds == abs_rect
                      and u.id == short_id(dn.res_id)), None)
        if match is not None:
            pins[id(dn)] = match
            used.add(id(match))
    return pins


def _without(nodes: list[ViewNode], claimed: set[int]) -> list[ViewNode]:
    result = []
    for node in nodes:
        if id(node) in claimed:
            continue
        if any(id(n) in claimed for n, _ in node.walk()):
            node = replace(node, children=_without(node.children, claimed))
        result.append(node)
    return result


def _convert(dn: DNode, un: ViewNode | None, origin: tuple[int, int], clip: Rect | None = None,
             pins: dict[int, ViewNode] | None = None) -> ViewNode:
    if un is None and pins:
        un = pins.get(id(dn))
    if dn.rel is not None:
        bounds = dn.rel.offset(*origin)
    elif _children_extent(dn).width > 0:
        bounds = _children_extent(dn).offset(*origin)  # root: uiautomator bounds may be clipped
    elif un is not None and un.bounds is not None:
        bounds = un.bounds
    else:
        bounds = Rect(*origin, *origin)
    exact = un is not None and un.bounds is not None and un.bounds.size == bounds.size
    if exact:
        bounds = un.bounds  # uiautomator knows scroll offsets; dumpsys does not

    props = {"dumpsys": _dumpsys_props(dn)}
    sources = ["dumpsys"]
    if un is not None:
        props["uiautomator"] = dict(un.props.get("uiautomator", {}))
        sources.append("uiautomator")
    node = ViewNode(class_name=dn.class_name, id=short_id(dn.res_id), bounds=bounds,
                    visibility=dn.visibility, text=un.text if un else None,
                    sources=sources, props=props)

    u_children = un.children if un is not None else []
    child_clip = un.bounds if un is not None and un.bounds is not None else clip
    if un is not None and dn.class_name.endswith(COMPOSE_HOST_SUFFIX):
        child_origin = (bounds.left, bounds.top)
        host_pins = {**(pins or {}), **_interop_pins(dn, child_origin, u_children)}
        claimed = {id(u) for u in host_pins.values()}
        node.children = ([_convert(c, None, child_origin, child_clip, host_pins) for c in dn.children]
                         + _without(u_children, claimed))
        return node
    pairs, leftovers = match_children(dn.children, u_children, (bounds.left, bounds.top))
    implied = _implied_origin(pairs)
    visible = un.bounds if un is not None else None
    if visible is not None and (visible.width <= 0 or visible.height <= 0):
        visible = None  # reported empty: not on screen at all, nothing to learn from it
    if not exact and visible is not None and dn.rel is not None:
        bounds = _locate(bounds, visible, implied, clip)
        node.bounds = bounds
    child_origin = implied or (bounds.left, bounds.top)
    node.children = [_convert(d, u, child_origin, child_clip, pins) for d, u in pairs] + leftovers
    return node
