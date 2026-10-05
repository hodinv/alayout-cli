from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from layoutcli.model import ViewNode

COMPARED_PROPS = ("content-desc", "clickable", "enabled", "checked", "selected", "focused")


@dataclass(eq=False)
class NodeChange:
    path: str
    a: ViewNode
    b: ViewNode
    fields: dict[str, tuple[str, str]]


@dataclass
class DiffResult:
    added: list[tuple[str, ViewNode]] = field(default_factory=list)
    removed: list[tuple[str, ViewNode]] = field(default_factory=list)
    changed: list[NodeChange] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def _key(node: ViewNode) -> str:
    return f"#{node.id}" if node.id else node.short_class


def node_paths(root: ViewNode) -> dict[str, ViewNode]:
    paths: dict[str, ViewNode] = {}

    def visit(node: ViewNode, path: str) -> None:
        paths[path] = node
        seen: Counter[str] = Counter()
        for child in node.children:
            key = _key(child)
            seen[key] += 1
            # the first occurrence is never numbered, so it keeps its path when siblings appear
            segment = key if seen[key] == 1 else f"{key}[{seen[key]}]"
            visit(child, ("" if path == "/" else path) + "/" + segment)

    visit(root, "/")
    return paths


def _parent(path: str) -> str:
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def _fields(node: ViewNode) -> dict[str, str]:
    values = {"class": node.class_name, "bounds": str(node.bounds) if node.bounds else "",
              "visibility": node.visibility, "text": node.text or ""}
    ui = node.props.get("uiautomator", {})
    for key in COMPARED_PROPS:
        if key in ui:
            values[key] = ui[key]
    return values


def diff_snapshots(a: ViewNode, b: ViewNode, compare_class: bool = True) -> DiffResult:
    pa, pb = node_paths(a), node_paths(b)
    result = DiffResult()
    for path, node in pa.items():
        if path not in pb:
            if _parent(path) in pb or path == "/":
                result.removed.append((path, node))
            continue
        fa, fb = _fields(node), _fields(pb[path])
        if not compare_class:
            fa.pop("class"), fb.pop("class")
        changed = {k: (fa[k], fb[k]) for k in fa if k in fb and fa[k] != fb[k]}
        if changed:
            result.changed.append(NodeChange(path, node, pb[path], changed))
    for path, node in pb.items():
        if path not in pa and _parent(path) in pa:
            result.added.append((path, node))
    return result
