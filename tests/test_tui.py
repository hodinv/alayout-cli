import asyncio
from io import BytesIO

from helpers import views_raw, views_snapshot
from PIL import Image
from textual.widgets import DataTable, Tree

from layoutcli.checks import run_checks
from layoutcli.snapshot_io import save_capture
from layoutcli.tui.app import LayoutApp


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


def test_unreadable_screenshot_shows_hint(tmp_path):
    snap = views_snapshot()
    save_capture(views_raw(), snap, tmp_path)  # helpers PNG_BYTES is a signature only, not a real image

    async def scenario(app, pilot):
        await pilot.press("p")
        assert "no screenshot" in app.query_one("#wire").render().plain
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
