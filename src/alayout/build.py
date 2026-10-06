from __future__ import annotations

import xml.etree.ElementTree as ET

from alayout.agent import AgentError, parse_agent_dump
from alayout.capture import RawCapture
from alayout.compose import apply_compose_names, compose_nodes, graft_compose_tree
from alayout.merge import merge, select_window
from alayout.model import Snapshot, ViewNode
from alayout.parse.dumpsys import parse_dumpsys
from alayout.parse.uiautomator import parse_uiautomator
from alayout.parse.wm import parse_wm_density, parse_wm_size

DEFAULT_SCREEN = (1080, 1920)


class BuildError(Exception):
    pass


def build_snapshot(raw: RawCapture, captured_at: str, show_ghosts: bool = False) -> Snapshot:
    caps: dict[str, str] = {}

    dump = parse_dumpsys(raw.dumpsys_text) if raw.dumpsys_text else None
    if dump is None:
        caps["dumpsys"] = raw.errors.get("dumpsys", "no activity view hierarchy found")
    elif not dump.resumed:
        caps["dumpsys"] = f"no resumed activity (screen locked?); showing {dump.activity or dump.package}"
    else:
        caps["dumpsys"] = "ok"

    ui_roots = []
    if raw.uiautomator_xml:
        try:
            ui_roots = parse_uiautomator(raw.uiautomator_xml)
            caps["uiautomator"] = "ok" if ui_roots else "empty hierarchy"
        except ET.ParseError as e:
            caps["uiautomator"] = f"invalid XML: {e}"
    else:
        caps["uiautomator"] = raw.errors.get("uiautomator", "not captured")

    caps["screenshot"] = "ok" if raw.screenshot_png else raw.errors.get("screenshot", "not captured")

    if dump is None and not ui_roots:
        raise BuildError("no view hierarchy captured: "
                         f"dumpsys: {caps['dumpsys']}; uiautomator: {caps['uiautomator']}")

    window, rejected = select_window(ui_roots, dump)
    if rejected and ui_roots:
        caps["uiautomator"] = rejected
    root = merge(dump, [window] if window is not None else [])
    _apply_names(raw, root, caps, show_ghosts)
    screen = _screen_size(parse_wm_size(raw.wm_size or ""), root)
    package = dump.package if dump else root.props.get("uiautomator", {}).get("package")
    return Snapshot(
        root=root, screen=screen, density=parse_wm_density(raw.wm_density or ""),
        package=package, activity=dump.activity if dump else None,
        device=dict(raw.device), captured_at=captured_at, capabilities=caps)


def _apply_names(raw: RawCapture, root: ViewNode, caps: dict[str, str],
                 show_ghosts: bool = False) -> None:
    """The composable names the agent read inside the app, or why they are missing."""
    if not raw.compose_json:
        if raw.errors.get("compose"):
            caps["compose"] = raw.errors["compose"]
        return
    try:
        dump = parse_agent_dump(raw.compose_json)
    except AgentError as e:
        caps["compose"] = str(e)
        return
    # with group ids the agent lets us rebuild the real composable tree under each Compose host;
    # an older agent (no ids) falls back to naming the flat semantics nodes in place
    named = graft_compose_tree(root, dump.hits, show_ghosts)
    if named is None:
        named = apply_compose_names(root, dump.hits)
    total = len(compose_nodes(root))
    caps["compose"] = f"ok ({named} of {total} Compose views named)" if total else f"ok ({named})"
    if dump.process and dump.package and dump.process != dump.package:
        caps["compose"] += f"; agent ran in process {dump.process}"  # a non-default (e.g. :foo) process
    if dump.errors:
        caps["compose"] += "; " + "; ".join(dump.errors)


def _screen_size(wm: tuple[int, int] | None, root: ViewNode) -> tuple[int, int]:
    """`wm size` reports the natural orientation; follow the root view when rotated."""
    has_bounds = root.bounds is not None and root.bounds.width > 0 and root.bounds.height > 0
    if wm is None:
        return root.bounds.size if has_bounds else DEFAULT_SCREEN
    w, h = wm
    if has_bounds and (root.bounds.width > root.bounds.height) != (w > h):
        return (h, w)
    return wm
