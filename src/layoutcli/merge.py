from __future__ import annotations

from layoutcli.model import Rect, ViewNode, short_id
from layoutcli.parse.dumpsys import DNode, DumpsysResult

COMPOSE_HOST_SUFFIX = "AndroidComposeView"


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


def _convert(dn: DNode, un: ViewNode | None, origin: tuple[int, int]) -> ViewNode:
    if dn.rel is not None:
        bounds = dn.rel.offset(*origin)
    elif _children_extent(dn).width > 0:
        bounds = _children_extent(dn).offset(*origin)  # root: uiautomator bounds may be clipped
    elif un is not None and un.bounds is not None:
        bounds = un.bounds
    else:
        bounds = Rect(*origin, *origin)
    if un is not None and un.bounds is not None and un.bounds.size == bounds.size:
        bounds = un.bounds  # uiautomator knows scroll offsets; dumpsys does not

    props = {"dumpsys": _dumpsys_props(dn)}
    sources = ["dumpsys"]
    if un is not None:
        props["uiautomator"] = dict(un.props.get("uiautomator", {}))
        sources.append("uiautomator")
    node = ViewNode(class_name=dn.class_name, id=short_id(dn.res_id), bounds=bounds,
                    visibility=dn.visibility, text=un.text if un else None,
                    sources=sources, props=props)

    child_origin = (bounds.left, bounds.top)
    u_children = un.children if un is not None else []
    if un is not None and dn.class_name.endswith(COMPOSE_HOST_SUFFIX):
        node.children = [_convert(c, None, child_origin) for c in dn.children] + list(u_children)
        return node
    pairs, leftovers = match_children(dn.children, u_children, child_origin)
    node.children = [_convert(d, u, child_origin) for d, u in pairs] + leftovers
    return node
