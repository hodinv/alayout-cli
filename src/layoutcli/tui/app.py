from __future__ import annotations

from pathlib import Path

from PIL import Image
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Tree
from textual.widgets.tree import TreeNode

from layoutcli.checks import run_checks
from layoutcli.format import node_label, node_rows
from layoutcli.model import Snapshot, ViewNode
from layoutcli.screenshot import render_screenshot
from layoutcli.search import find_matches, keep_set
from layoutcli.wireframe import render_wireframe


def _load_image(snapshot: Snapshot, base_dir: Path | None) -> Image.Image | None:
    if base_dir is None or not snapshot.screenshot:
        return None
    try:
        with Image.open(base_dir / snapshot.screenshot) as img:
            return img.convert("RGB")
    except OSError:
        return None


class Wireframe(Widget):
    """Preview pane: box wireframe or the device screenshot, with the selected view highlighted."""

    def __init__(self, snapshot: Snapshot, image: Image.Image | None = None, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = snapshot
        self.image = image
        self.selected: ViewNode | None = None
        self.mode = "wireframe"

    def select(self, node: ViewNode | None) -> None:
        self.selected = node
        self.refresh()

    def toggle_mode(self) -> None:
        self.mode = "screenshot" if self.mode == "wireframe" else "wireframe"
        self.refresh()

    def render(self) -> Text:
        size = self.content_size
        if self.mode == "screenshot":
            if self.image is None:
                return Text("no screenshot in this snapshot (p: back to wireframe)", style="dim")
            return render_screenshot(self.image, self.snapshot.screen, size.width, size.height, self.selected)
        return render_wireframe(self.snapshot.root, self.snapshot.screen,
                                size.width, size.height, self.selected)


class LayoutApp(App):
    TITLE = "layoutcli"
    CSS = """
    #tree { width: 1fr; }
    #right { width: 1fr; }
    #props, #issues { height: 2fr; }
    #issues { display: none; }
    #wire { height: 3fr; border: round $primary; }
    #search { dock: bottom; display: none; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "search", "Search"),
        Binding("n", "next_match", "Next"),
        Binding("N", "prev_match", "Prev"),
        Binding("f", "filter", "Filter"),
        Binding("p", "preview", "Preview"),
        Binding("c", "checks", "Checks"),
        Binding("escape", "close_search", "Close", show=False),
    ]

    def __init__(self, snapshot: Snapshot, base_dir: Path | None = None):
        super().__init__()
        self.snapshot = snapshot
        self.selected: ViewNode | None = None
        self.issues = run_checks(snapshot)
        self._warned = {i.node for i in self.issues if i.severity == "warning"}
        self._tree_nodes: dict[ViewNode, TreeNode[ViewNode]] = {}
        self._image = _load_image(snapshot, base_dir)
        self._query = ""
        self._matches: list[ViewNode] = []
        self._match_index = -1
        self._filtered = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Tree(self._label(self.snapshot.root), data=self.snapshot.root, id="tree")
            with Vertical(id="right"):
                yield DataTable(id="props", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="issues", cursor_type="row", zebra_stripes=True)
                yield Wireframe(self.snapshot, self._image, id="wire")
        yield Input(placeholder="search id, class, text, content-desc  (Enter: find, Esc: close)", id="search")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.snapshot.activity or self.snapshot.package or ""
        self.query_one("#props", DataTable).add_columns("source", "property", "value")
        table = self.query_one("#issues", DataTable)
        table.add_columns("severity", "check", "view", "message")
        for index, issue in enumerate(self.issues):
            table.add_row(Text(issue.severity), Text(issue.check), node_label(issue.node),
                          Text(issue.message), key=str(index))
        self._populate(None)
        self.query_one("#tree", Tree).focus()
        self.select(self.snapshot.root)

    # --- tree -----------------------------------------------------------------------------

    def _label(self, node: ViewNode) -> Text:
        return node_label(node, warning=node in self._warned)

    def _populate(self, keep: set[ViewNode] | None) -> None:
        tree = self.query_one("#tree", Tree)
        tree.clear()
        self._tree_nodes = {self.snapshot.root: tree.root}
        self._add_children(tree.root, self.snapshot.root, keep)
        tree.root.expand()

    def _add_children(self, tree_node: TreeNode[ViewNode], view: ViewNode, keep: set[ViewNode] | None) -> None:
        for child in view.children:
            if keep is not None and child not in keep:
                continue
            if child.children:
                branch = tree_node.add(self._label(child), data=child, expand=True)
                self._tree_nodes[child] = branch
                self._add_children(branch, child, keep)
            else:
                self._tree_nodes[child] = tree_node.add_leaf(self._label(child), data=child)

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

    def _jump(self, node: ViewNode) -> None:
        tree_node = self._tree_nodes.get(node)
        if tree_node is not None:
            self.query_one("#tree", Tree).move_cursor(tree_node)
        self.select(node)

    # --- search & filter --------------------------------------------------------------------

    def action_search(self) -> None:
        box = self.query_one("#search", Input)
        box.display = True
        box.focus()

    def action_close_search(self) -> None:
        self.query_one("#search", Input).display = False
        self.query_one("#tree", Tree).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._query = event.value.strip()
        self._matches = find_matches(self.snapshot.root, self._query)
        self._match_index = -1
        self.action_close_search()
        if self._filtered:
            self._populate(keep_set(self.snapshot.root, self._query) if self._query else None)
        self._step_match(1)

    def action_next_match(self) -> None:
        self._step_match(1)

    def action_prev_match(self) -> None:
        self._step_match(-1)

    def _step_match(self, step: int) -> None:
        if not self._matches:
            if self._query:
                self.notify(f"no matches for {self._query!r}", severity="warning")
            return
        self._match_index = (self._match_index + step) % len(self._matches)
        self._jump(self._matches[self._match_index])
        self.sub_title = f"{self._query!r}: {self._match_index + 1}/{len(self._matches)}"

    def action_filter(self) -> None:
        if not self._query:
            self.notify("search first (/), then f filters the tree to the matches")
            return
        self._filtered = not self._filtered
        self._populate(keep_set(self.snapshot.root, self._query) if self._filtered else None)
        if self.selected is not None and self.selected in self._tree_nodes:
            self.query_one("#tree", Tree).move_cursor(self._tree_nodes[self.selected])

    # --- preview & checks -------------------------------------------------------------------

    def action_preview(self) -> None:
        self.query_one("#wire", Wireframe).toggle_mode()

    def action_checks(self) -> None:
        issues = self.query_one("#issues", DataTable)
        issues.display = not issues.display
        self.query_one("#props", DataTable).display = not issues.display
        (issues if issues.display else self.query_one("#tree", Tree)).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "issues":
            self._jump(self.issues[int(event.row_key.value)].node)
