from __future__ import annotations

import xml.etree.ElementTree as ET

from layoutcli.capture import RawCapture
from layoutcli.merge import merge, select_window
from layoutcli.model import Snapshot, ViewNode
from layoutcli.parse.dumpsys import parse_dumpsys
from layoutcli.parse.uiautomator import parse_uiautomator
from layoutcli.parse.wm import parse_wm_density, parse_wm_size

DEFAULT_SCREEN = (1080, 1920)


class BuildError(Exception):
    pass


def build_snapshot(raw: RawCapture, captured_at: str) -> Snapshot:
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
    screen = _screen_size(parse_wm_size(raw.wm_size or ""), root)
    package = dump.package if dump else root.props.get("uiautomator", {}).get("package")
    return Snapshot(
        root=root, screen=screen, density=parse_wm_density(raw.wm_density or ""),
        package=package, activity=dump.activity if dump else None,
        device=dict(raw.device), captured_at=captured_at, capabilities=caps)


def _screen_size(wm: tuple[int, int] | None, root: ViewNode) -> tuple[int, int]:
    """`wm size` reports the natural orientation; follow the root view when rotated."""
    has_bounds = root.bounds is not None and root.bounds.width > 0 and root.bounds.height > 0
    if wm is None:
        return root.bounds.size if has_bounds else DEFAULT_SCREEN
    w, h = wm
    if has_bounds and (root.bounds.width > root.bounds.height) != (w > h):
        return (h, w)
    return wm
