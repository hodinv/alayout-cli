from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Tree
from textual.widgets.tree import TreeNode

from layoutcli.format import node_label, node_rows
from layoutcli.model import Snapshot, ViewNode
from layoutcli.wireframe import render_wireframe


class Wireframe(Widget):
    def __init__(self, snapshot: Snapshot, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = snapshot
        self.selected: ViewNode | None = None

    def select(self, node: ViewNode | None) -> None:
        self.selected = node
        self.refresh()

    def render(self) -> Text:
        size = self.content_size
        return render_wireframe(self.snapshot.root, self.snapshot.screen,
                                size.width, size.height, self.selected)


def _add_children(tree_node: TreeNode[ViewNode], view: ViewNode) -> None:
    for child in view.children:
        if child.children:
            branch = tree_node.add(node_label(child), data=child, expand=True)
            _add_children(branch, child)
        else:
            tree_node.add_leaf(node_label(child), data=child)


class LayoutApp(App):
    TITLE = "layoutcli"
    CSS = """
    #tree { width: 1fr; }
    #right { width: 1fr; }
    #props { height: 2fr; }
    #wire { height: 3fr; border: round $primary; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, snapshot: Snapshot):
        super().__init__()
        self.snapshot = snapshot
        self.selected: ViewNode | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Tree(node_label(self.snapshot.root), data=self.snapshot.root, id="tree")
            with Vertical(id="right"):
                yield DataTable(id="props", cursor_type="row", zebra_stripes=True)
                yield Wireframe(self.snapshot, id="wire")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.snapshot.activity or self.snapshot.package or ""
        self.query_one("#props", DataTable).add_columns("source", "property", "value")
        tree = self.query_one("#tree", Tree)
        _add_children(tree.root, self.snapshot.root)
        tree.root.expand()
        tree.focus()
        self.select(self.snapshot.root)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data is not None:
            self.select(event.node.data)

    def select(self, node: ViewNode) -> None:
        self.selected = node
        table = self.query_one("#props", DataTable)
        table.clear()
        for row in node_rows(node, self.snapshot.density):
            table.add_row(*(Text(cell) for cell in row))  # literal: app strings may contain [markup]
        self.query_one("#wire", Wireframe).select(node)
