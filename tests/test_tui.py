import asyncio

from helpers import views_snapshot
from textual.widgets import DataTable, Tree

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
