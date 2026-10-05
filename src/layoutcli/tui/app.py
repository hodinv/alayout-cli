from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Static, Tree
from textual.widgets.tree import TreeNode

from layoutcli.checks import run_checks
from layoutcli.compose import compose_nodes, infer_components
from layoutcli.format import compose_kind, node_label, node_rows
from layoutcli.model import Snapshot, ViewNode
from layoutcli.screenshot import annotate, fit_image, render_screenshot
from layoutcli.search import find_matches, keep_set
from layoutcli.snapshot_io import load_apk_index
from layoutcli.wireframe import render_wireframe


NO_SCREENSHOT = "no screenshot in this snapshot"


def _load_image(snapshot: Snapshot, base_dir: Path | None) -> tuple[Image.Image | None, str]:
    """The screenshot, or None and why not."""
    if base_dir is None or not snapshot.screenshot:
        return None, NO_SCREENSHOT
    try:
        with Image.open(base_dir / snapshot.screenshot) as img:
            return img.convert("RGB"), ""
    except OSError as e:
        return None, f"cannot read the screenshot {snapshot.screenshot}: {e}"


def open_file(path: Path) -> None:
    """Show a file in the system's default viewer."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ScreenshotView(Widget):
    """Half-block screenshot sized to the widget, resized only when the widget size changes."""

    def __init__(self, image: Image.Image | None, screen: tuple[int, int], **kwargs):
        super().__init__(**kwargs)
        self.image = image
        self.device_screen = screen
        self.selected: ViewNode | None = None
        self._fitted: tuple[tuple[int, int], Image.Image | None] | None = None

    def screenshot(self, cols: int, rows: int) -> Text:
        if self._fitted is None or self._fitted[0] != (cols, rows):
            self._fitted = ((cols, rows), fit_image(self.image, cols, rows))
        return render_screenshot(self.image, self.device_screen, cols, rows, self.selected, self._fitted[1])

    def render(self) -> Text:
        size = self.content_size
        return self.screenshot(size.width, size.height)


class Wireframe(ScreenshotView):
    """Preview pane: box wireframe or the device screenshot, with the selected view highlighted."""

    def __init__(self, snapshot: Snapshot, image: Image.Image | None = None, image_note: str = NO_SCREENSHOT,
                 **kwargs):
        super().__init__(image, snapshot.screen, **kwargs)
        self.snapshot = snapshot
        self.image_note = image_note
        self.selected_label: str | None = None
        self.mode = "wireframe"

    def select(self, node: ViewNode | None, label: str | None = None) -> None:
        self.selected = node
        self.selected_label = label
        self.refresh()

    def toggle_mode(self) -> None:
        self.mode = "screenshot" if self.mode == "wireframe" else "wireframe"
        self.refresh()

    def render(self) -> Text:
        size = self.content_size
        if self.mode == "screenshot":
            if self.image is None:
                return Text(f"{self.image_note} (p: back to wireframe)", style="dim")
            return self.screenshot(size.width, size.height)
        return render_wireframe(self.snapshot.root, self.snapshot.screen,
                                size.width, size.height, self.selected, self.selected_label)


class LayoutXmlScreen(ModalScreen):
    """Decoded layout XML from the APK, with the selected view's line highlighted."""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("q", "dismiss", "Close")]
    DEFAULT_CSS = """
    LayoutXmlScreen { align: center middle; }
    #xml { width: 90%; height: 85%; border: round $primary; background: $surface; }
    """

    def __init__(self, title: str, layouts: list[tuple[str, str]], view_id: str):
        super().__init__()
        self.title_text = title
        self.body = Text()
        self.first_match: int | None = None
        marker = f'android:id="@id/{view_id}"'
        lines: list[tuple[str, str]] = []
        for file, xml in layouts:
            if len(layouts) > 1:
                if lines:
                    lines.append(("", ""))
                lines.append((f"<!-- {file} -->", "bold"))
            lines += [(line, "bold reverse" if marker in line else "") for line in xml.splitlines()]
        for i, (line, style) in enumerate(lines):
            if i:
                self.body.append("\n")
            self.body.append(line, style=style)
            if style == "bold reverse" and self.first_match is None:
                self.first_match = i

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="xml"):
            yield Static(self.body)

    def on_mount(self) -> None:
        box = self.query_one("#xml", VerticalScroll)
        box.border_title = self.title_text
        if self.first_match is not None:  # keep a few lines of context above the highlighted view
            self.call_after_refresh(box.scroll_to, y=max(0, self.first_match - 5), animate=False)


class ScreenshotScreen(ModalScreen):
    """The screenshot as large as the terminal allows, with the selected view outlined."""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("q", "dismiss", "Close"),
                Binding("s", "dismiss", "Close", show=False),
                Binding("o", "app.open_screenshot", "Open PNG")]
    DEFAULT_CSS = """
    ScreenshotScreen { align: center middle; }
    #shot { width: 100%; height: 100%; border: round $primary; background: $surface; }
    """

    def __init__(self, image: Image.Image, screen: tuple[int, int], selected: ViewNode | None, title: str):
        super().__init__()
        self.image, self.screen_size, self.selected, self.title_text = image, screen, selected, title

    def compose(self) -> ComposeResult:
        view = ScreenshotView(self.image, self.screen_size, id="shot")
        view.selected = self.selected
        yield view

    def on_mount(self) -> None:
        shot = self.query_one("#shot")
        shot.border_title = self.title_text
        shot.border_subtitle = "esc: close  o: open PNG in image viewer"


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
        Binding("s", "screenshot", "Screenshot"),
        Binding("o", "open_screenshot", "Open PNG"),
        Binding("c", "checks", "Checks"),
        Binding("x", "layout_xml", "Layout XML"),
        Binding("escape", "close_search", "Close", show=False),
    ]

    def __init__(self, snapshot: Snapshot, base_dir: Path | None = None):
        super().__init__()
        self.snapshot = snapshot
        self.selected: ViewNode | None = None
        self.issues = sorted(run_checks(snapshot), key=lambda i: i.severity != "warning")
        self.components = infer_components(snapshot.root)
        self._compose = compose_nodes(snapshot.root)
        self._aliases: dict[ViewNode, str] = {}
        self._warned = {i.node for i in self.issues if i.severity == "warning"}
        self._tree_nodes: dict[ViewNode, TreeNode[ViewNode]] = {}
        self._image, self._image_note = _load_image(snapshot, base_dir)
        self._apk = load_apk_index(base_dir) if base_dir is not None else None
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
                yield Wireframe(self.snapshot, self._image, self._image_note, id="wire")
        yield Input(placeholder="search id, class, text, content-desc  (Enter: find, Esc: close)", id="search")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self._default_subtitle()
        self.query_one("#props", DataTable).add_columns("source", "property", "value")
        table = self.query_one("#issues", DataTable)
        table.add_columns("severity", "check", "view", "message")
        for index, issue in enumerate(self.issues):
            table.add_row(Text(issue.severity), Text(issue.check),
                          self._plain_label(issue.node),
                          Text(issue.message), key=str(index))
        self._populate(None)
        self.query_one("#tree", Tree).focus()
        self.select(self.snapshot.root)

    # --- tree -----------------------------------------------------------------------------

    def _label(self, node: ViewNode) -> Text:
        return node_label(node, warning=node in self._warned, component=self.components.get(node),
                          in_compose=node in self._compose)

    def _plain_label(self, node: ViewNode) -> Text:
        return node_label(node, component=self.components.get(node), in_compose=node in self._compose)

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
        component = self.components.get(node)
        for row in node_rows(node, self.snapshot.density, component):
            table.add_row(*(Text(cell) for cell in row))  # literal: app strings may contain [markup]
        wire_label = compose_kind(node, component)[0] if node in self._compose else None
        self.query_one("#wire", Wireframe).select(node, wire_label)

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
        query = event.value.strip()
        self.action_close_search()
        if not query:  # empty search clears search and filter
            self._query, self._matches, self._match_index = "", [], -1
            if self._filtered:
                self._filtered = False
                self._populate(None)
            self.sub_title = self._default_subtitle()
            return
        matches = find_matches(self.snapshot.root, query, self._search_aliases())
        if not matches:  # keep the previous search, filter and tree
            self.notify(f"no matches for {query!r}", severity="warning")
            return
        self._query, self._matches, self._match_index = query, matches, -1
        if self._filtered:
            self._populate(keep_set(self.snapshot.root, self._query, self._search_aliases()))
        self._step_match(1)

    def _search_aliases(self) -> dict[ViewNode, str]:
        if not self._aliases:
            self._aliases = {node: self._plain_label(node).plain for node in self._compose}
        return self._aliases

    def _default_subtitle(self) -> str:
        return self.snapshot.activity or self.snapshot.package or ""

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
        self._populate(keep_set(self.snapshot.root, self._query, self._search_aliases()) if self._filtered else None)
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

    def action_layout_xml(self) -> None:
        node = self.selected
        mapped = self._apk is not None and node is not None and node.id and "apk" in node.props
        files = [f for f in (self._apk.ids.get(node.id, []) if mapped else []) if f in self._apk.layouts]
        if not files:
            self.notify("no layout XML for this view (capture with --apk PATH or --apk device)")
            return
        title = files[0] if len(files) == 1 else f"{len(files)} layouts"
        self.push_screen(LayoutXmlScreen(f"{title}  (#{node.id})",
                                         [(f, self._apk.layouts[f]) for f in files], node.id))

    def _selected_title(self) -> str:
        return self._plain_label(self.selected).plain if self.selected is not None else "screenshot"

    def action_screenshot(self) -> None:
        if self._image is None:
            self.notify(self._image_note, severity="warning")
            return
        self.push_screen(ScreenshotScreen(self._image, self.snapshot.screen, self.selected, self._selected_title()))

    def action_open_screenshot(self) -> None:
        if self._image is None:
            self.notify(self._image_note, severity="warning")
            return
        try:
            with tempfile.NamedTemporaryFile(prefix="layoutcli-", suffix=".png", delete=False) as f:
                annotate(self._image, self.snapshot.screen, self.selected).save(f, format="PNG")
            open_file(Path(f.name))
        except OSError as e:
            self.notify(f"cannot open the screenshot: {e}", severity="error")
            return
        self.notify(f"opened {f.name}")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "issues":
            self._jump(self.issues[int(event.row_key.value)].node)
