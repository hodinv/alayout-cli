from __future__ import annotations

from layoutcli.model import ViewNode


def node_matches(node: ViewNode, query: str) -> bool:
    q = query.casefold()
    fields = (node.class_name, node.id or "", node.text or "",
              node.props.get("uiautomator", {}).get("content-desc", ""))
    return any(q in f.casefold() for f in fields)


def find_matches(root: ViewNode, query: str) -> list[ViewNode]:
    if not query:
        return []
    return [node for node, _ in root.walk() if node_matches(node, query)]


def keep_set(root: ViewNode, query: str) -> set[ViewNode]:
    keep: set[ViewNode] = set()
    if not query:
        return keep

    def visit(node: ViewNode, ancestors: tuple[ViewNode, ...]) -> None:
        if node_matches(node, query):
            keep.add(node)
            keep.update(ancestors)
        for child in node.children:
            visit(child, ancestors + (node,))

    visit(root, ())
    return keep
