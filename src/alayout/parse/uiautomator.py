from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from alayout.model import Rect, ViewNode, short_id

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_bounds(s: str) -> Rect | None:
    m = _BOUNDS_RE.fullmatch(s.strip())
    return Rect(*(int(g) for g in m.groups())) if m else None


def parse_uiautomator(xml: str) -> list[ViewNode]:
    """Parse `uiautomator dump` output into one ViewNode tree per top-level window node."""
    root = ET.fromstring(xml.strip().encode("utf-8"))
    return [_convert(el) for el in root.findall("node")]


def _convert(el: ET.Element) -> ViewNode:
    attrs = dict(el.attrib)
    node = ViewNode(
        class_name=attrs.get("class") or "?",
        id=short_id(attrs.get("resource-id")),
        bounds=parse_bounds(attrs.get("bounds", "")),
        text=attrs.get("text") or None,
        sources=["uiautomator"],
        props={"uiautomator": attrs},
    )
    node.children = [_convert(child) for child in el.findall("node")]
    return node
