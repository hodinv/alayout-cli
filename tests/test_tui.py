import asyncio
from io import BytesIO

from helpers import views_raw, views_snapshot
from PIL import Image
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Tree

from alayout.apk import ApkIndex, apply_index
from alayout.checks import run_checks
from alayout.snapshot_io import save_apk_index, save_capture
from alayout.tui.app import LayoutApp, LayoutTree, PropsTable


def tree_nodes(node):
    yield node
    for child in node.children:
        yield from tree_nodes(child)


def test_selecting_a_node_updates_properties_and_wireframe():
    async def scenario():
        app = LayoutApp(views_snapshot())
        async with app.run_test(size=(140, 45)) as pilot:
            tree = app.query_one("#tree", Tree)
            target = next(n for n in tree_nodes(tree.root) if n.data is not None and n.data.id == "toolbar")
            tree.move_cursor(target)
            await pilot.pause()
            assert app.selected is target.data
            table = app.query_one("#props", DataTable)
            values = [str(table.get_row_at(i)[2]) for i in range(table.row_count)]
            assert "com.google.android.material.appbar.MaterialToolbar" in values
            wire = app.query_one("#wire")
            assert wire.selected is target.data
            assert "MaterialToolbar"[:5] in wire.render().plain

    asyncio.run(scenario())


def test_tree_contains_every_node():
    async def scenario():
        snap = views_snapshot()
        app = LayoutApp(snap)
        async with app.run_test(size=(140, 45)):
            tree = app.query_one("#tree", Tree)
            assert sum(1 for _ in tree_nodes(tree.root)) == sum(1 for _ in snap.root.walk())

    asyncio.run(scenario())


def test_view_text_with_markup_brackets_is_shown_literally():
    async def scenario():
        snap = views_snapshot()
        title = next(n for n, _ in snap.root.walk() if n.text == "Demo")
        title.text = "[/] close [b]x"
        app = LayoutApp(snap)
        async with app.run_test(size=(140, 45)) as pilot:
            app.select(title)
            await pilot.pause()
            table = app.query_one("#props", DataTable)
            values = [str(table.get_row_at(i)[2]) for i in range(table.row_count)]
            assert "[/] close [b]x" in values

    asyncio.run(scenario())



def run_app(snap, scenario, base_dir=None):
    async def main():
        app = LayoutApp(snap, base_dir=base_dir)
        async with app.run_test(size=(140, 45)) as pilot:
            await scenario(app, pilot)
    asyncio.run(main())


def test_preview_toggle_without_screenshot_shows_hint():
    async def scenario(app, pilot):
        await pilot.press("p")
        wire = app.query_one("#wire")
        assert wire.mode == "screenshot"
        assert "no screenshot" in wire.render().plain
        await pilot.press("p")
        assert wire.mode == "wireframe"
    run_app(views_snapshot(), scenario)


def test_preview_renders_real_png(tmp_path):
    raw = views_raw()
    buf = BytesIO()
    Image.new("RGB", (108, 240), (10, 20, 30)).save(buf, format="PNG")
    raw.screenshot_png = buf.getvalue()
    snap = views_snapshot()
    save_capture(raw, snap, tmp_path)

    async def scenario(app, pilot):
        await pilot.press("p")
        assert "▀" in app.query_one("#wire").render().plain
    run_app(snap, scenario, base_dir=tmp_path)


def test_search_jumps_and_cycles_matches():
    async def scenario(app, pilot):
        await pilot.press("slash", *"item_title", "enter")
        await pilot.pause()
        first = app.selected
        assert first.text == "First item"
        await pilot.press("n")
        await pilot.pause()
        assert app.selected.text == "Second item"
        await pilot.press("n")
        await pilot.pause()
        assert app.selected is first
        await pilot.press("N")
        await pilot.pause()
        assert app.selected.text == "Second item"
    run_app(views_snapshot(), scenario)


def test_filter_shows_matches_and_ancestors_only():
    async def scenario(app, pilot):
        tree = app.query_one("#tree", Tree)
        total = sum(1 for _ in tree_nodes(tree.root))
        await pilot.press("f")  # before any search: notification only
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == total
        await pilot.press("slash", *"toolbar", "enter", "f")
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == 5
        await pilot.press("f")
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == total
    run_app(views_snapshot(), scenario)


def test_search_without_matches_keeps_selection():
    async def scenario(app, pilot):
        before = app.selected
        await pilot.press("slash", *"zzz", "enter")
        await pilot.pause()
        assert app.selected is before
    run_app(views_snapshot(), scenario)


def test_checks_list_and_badges():
    snap = views_snapshot()

    async def scenario(app, pilot):
        await pilot.press("c")
        await pilot.pause()
        issues = app.query_one("#issues", DataTable)
        assert issues.display and not app.query_one("#props").display
        assert issues.row_count == len(run_checks(snap))
        fab = next(n for n in tree_nodes(app.query_one("#tree", Tree).root)
                   if n.data is not None and n.data.id == "fab_small")
        assert "⚠" in str(fab.label)
        await pilot.press("enter")  # select first issue row → jump to its node
        await pilot.pause()
        assert app.selected is app.issues[0].node
    run_app(snap, scenario)


def test_no_match_search_while_filtered_keeps_tree():
    async def scenario(app, pilot):
        tree = app.query_one("#tree", Tree)
        await pilot.press("slash", *"toolbar", "enter", "f")
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == 5
        await pilot.press("slash", *"zzz", "enter")
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == 5
        assert "zzz" not in app.sub_title
    run_app(views_snapshot(), scenario)


def test_empty_search_while_filtered_clears_filter():
    async def scenario(app, pilot):
        tree = app.query_one("#tree", Tree)
        total = sum(1 for _ in tree_nodes(tree.root))
        await pilot.press("slash", *"toolbar", "enter", "f", "slash", *["backspace"] * 7, "enter")
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == total
        assert app._filtered is False
    run_app(views_snapshot(), scenario)


def test_x_shows_layout_xml_for_selected_view(tmp_path):
    snap = views_snapshot()
    index = ApkIndex(ids={"toolbar": ["res/layout/activity_main.xml"]},
                     layouts={"res/layout/activity_main.xml":
                              '<LinearLayout>\n    <TextView android:id="@id/toolbar"/>\n</LinearLayout>'})
    apply_index(snap.root, index)  # as a real --apk capture does
    save_capture(views_raw(), snap, tmp_path)
    save_apk_index(index, tmp_path)

    async def scenario(app, pilot):
        await pilot.press("slash", *"toolbar", "enter", "x")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "LayoutXmlScreen"
        assert 'android:id="@id/toolbar"' in screen.body.plain
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "LayoutXmlScreen"
    run_app(snap, scenario, base_dir=tmp_path)


def test_x_without_mapping_only_notifies():
    async def scenario(app, pilot):
        await pilot.press("x")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "LayoutXmlScreen"
    run_app(views_snapshot(), scenario)


def test_x_ignores_framework_id_even_if_apk_json_has_it(tmp_path):
    snap = views_snapshot()
    save_capture(views_raw(), snap, tmp_path)
    save_apk_index(ApkIndex(ids={"content": ["res/layout/abc_popup_menu_item_layout.xml"]},
                            layouts={"res/layout/abc_popup_menu_item_layout.xml": '<X android:id="@id/content"/>'}),
                   tmp_path)

    async def scenario(app, pilot):
        await pilot.press("slash", *"content", "enter")
        await pilot.pause()
        assert app.selected.id == "content"
        await pilot.press("x")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "LayoutXmlScreen"
    run_app(snap, scenario, base_dir=tmp_path)


def test_compose_components_shown_in_tree_and_properties():
    async def scenario(app, pilot):
        tree = app.query_one("#tree", Tree)
        button = next(n for n in tree_nodes(tree.root)
                      if n.data is not None and n.data.class_name == "android.widget.Button")
        assert str(button.label) == 'Button "Click me" 358x126'
        tree.move_cursor(button)
        await pilot.pause()
        table = app.query_one("#props", DataTable)
        rows = [tuple(str(c) for c in table.get_row_at(i)) for i in range(table.row_count)]
        assert ("compose", "component", "Button") in rows
        assert "Button" in app.query_one("#wire").render().plain
    run_app(views_snapshot(), scenario)


def test_tree_shows_compose_nodes_by_kind_and_search_finds_them():
    async def scenario(app, pilot):
        tree = app.query_one("#tree", Tree)
        labels = [str(n.label) for n in tree_nodes(tree.root)]
        assert 'Button "Click me" 358x126' in labels
        assert 'Text "Hello Compose" 458x60' in labels
        await pilot.press("slash", *"Click", "space", *"me", "enter")  # alias 'Button "Click me"' matches first
        await pilot.pause()
        assert app.selected.class_name == "android.widget.Button" and app.selected.text is None
    run_app(views_snapshot(), scenario)


def test_name_click_does_not_toggle_expand():
    # expand/collapse is only on the ▶/▼ arrow (or space), so selecting a name must not toggle it
    assert LayoutTree.auto_expand is False


def test_double_click_property_copies_its_value():
    async def scenario():
        app = LayoutApp(views_snapshot())
        async with app.run_test(size=(140, 45)) as pilot:
            table = app.query_one("#props", PropsTable)
            expected = table.get_cell_at(Coordinate(0, PropsTable.VALUE_COLUMN)).plain
            await pilot.click("#props", offset=(3, 1), times=2)  # first data row, double-click
            await pilot.pause()
            assert app.clipboard == expected

    asyncio.run(scenario())


def test_double_click_name_opens_the_png(monkeypatch):
    opened = []
    monkeypatch.setattr("alayout.tui.app.open_file", lambda path, env=None: opened.append(path))

    async def scenario():
        app = LayoutApp(views_snapshot())
        app._image = Image.new("RGB", (108, 240))  # a screenshot to annotate and open
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.click("#tree", offset=(20, 0), times=2)  # the root node's name
            await pilot.pause()
            assert len(opened) == 1 and str(opened[0]).endswith(".png")

    asyncio.run(scenario())


def test_arrow_click_toggles_but_name_click_does_not():
    async def scenario():
        app = LayoutApp(views_snapshot())
        async with app.run_test(size=(140, 45)) as pilot:
            tree = app.query_one("#tree", Tree)
            branch = next(b for b in tree.root.children if b.children)
            y = branch.line
            expanded = branch.is_expanded
            await pilot.click("#tree", offset=(4, y), times=1)  # the ▶/▼ arrow
            await pilot.pause()
            assert branch.is_expanded is not expanded  # arrow toggled it
            now = branch.is_expanded
            await pilot.click("#tree", offset=(14, y), times=1)  # the name
            await pilot.pause()
            assert app.selected is branch.data  # name selected it
            assert branch.is_expanded is now  # but did not toggle

    asyncio.run(scenario())


def test_node_at_prefers_a_composable_then_the_smallest():
    from alayout.model import Rect, Snapshot, ViewNode
    leaf = ViewNode("android.view.View", bounds=Rect(0, 0, 100, 100), sources=["uiautomator"],
                    props={"uiautomator": {}})
    tiny_native = ViewNode("android.widget.Button", bounds=Rect(40, 40, 60, 60), sources=["dumpsys"],
                           props={"dumpsys": {}})
    big = ViewNode("android.widget.FrameLayout", bounds=Rect(0, 0, 1000, 1000),
                   children=[leaf, tiny_native])
    host = ViewNode("androidx.compose.ui.platform.AndroidComposeView", bounds=Rect(0, 0, 1000, 1000),
                    children=[big])
    root = ViewNode("com.android.internal.policy.DecorView", bounds=Rect(0, 0, 1000, 1000),
                    children=[host])
    app = LayoutApp(Snapshot(root=root, screen=(1000, 1000), density=160))
    # (50,50) is inside leaf (composable) and tiny_native (smaller, native): the composable wins
    assert app._node_at(50, 50) is leaf
    # a point with no composable falls back to the smallest widget there
    assert app._node_at(500, 500) is big


def test_clicking_the_preview_selects_and_focuses_the_tree():
    async def scenario():
        app = LayoutApp(views_snapshot())
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.click("#wire", offset=(6, 6), times=1)
            await pilot.pause()
            assert isinstance(app.focused, Tree)  # focus moved to the tree
            assert app.selected is not None       # something under the click got selected

    asyncio.run(scenario())
