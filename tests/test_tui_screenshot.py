from io import BytesIO

from helpers import views_raw, views_snapshot
from PIL import Image
from test_tui import run_app
from textual.widgets import DataTable

from layoutcli.apk import ApkIndex, apply_index
from layoutcli.screenshot import HIGHLIGHT, annotate
from layoutcli.snapshot_io import save_apk_index, save_capture
from layoutcli.tui import app as tui_app


def snapshot_with_png(tmp_path, size=(108, 240)):
    raw = views_raw()
    buf = BytesIO()
    Image.new("RGB", size, (100, 100, 100)).save(buf, format="PNG")
    raw.screenshot_png = buf.getvalue()
    snap = views_snapshot()
    save_capture(raw, snap, tmp_path)
    return snap


def test_annotate_outlines_selection_and_dims_the_rest():
    snap = views_snapshot()
    image = Image.new("RGB", snap.screen, (100, 100, 100))
    node = next(n for n, _ in snap.root.walk() if n.id == "toolbar")
    out = annotate(image, snap.screen, node)
    assert out.size == image.size
    b = node.bounds
    assert out.getpixel((b.left, b.top)) == HIGHLIGHT
    assert out.getpixel((b.left + b.width // 2, b.top + b.height // 2)) == (100, 100, 100)
    assert max(out.getpixel((snap.screen[0] - 1, snap.screen[1] - 1))) < 100  # dimmed outside
    assert image.getpixel((b.left, b.top)) == (100, 100, 100)  # original untouched


def test_o_opens_annotated_png_in_system_viewer(tmp_path, monkeypatch):
    snap = snapshot_with_png(tmp_path, size=snap_size())
    opened = []
    monkeypatch.setattr(tui_app, "open_file", opened.append)

    async def scenario(app, pilot):
        await pilot.press("slash", *"toolbar", "enter", "o")
        await pilot.pause()
        assert len(opened) == 1
        with Image.open(opened[0]) as img:
            assert img.size == snap.screen
            b = app.selected.bounds
            assert img.convert("RGB").getpixel((b.left, b.top)) == HIGHLIGHT
    run_app(snap, scenario, base_dir=tmp_path)


def snap_size():
    return tuple(views_snapshot().screen)


def test_o_and_s_without_screenshot_only_notify(monkeypatch):
    opened, notes = [], []
    monkeypatch.setattr(tui_app, "open_file", opened.append)

    async def scenario(app, pilot):
        app.notify = lambda message, **kw: notes.append(message)
        await pilot.press("o", "s")
        await pilot.pause()
        assert not opened
        assert app.screen.__class__.__name__ != "ScreenshotScreen"
        assert len(notes) == 2 and all("screenshot" in n for n in notes)
    run_app(views_snapshot(), scenario)


def test_s_shows_full_screen_screenshot(tmp_path):
    snap = snapshot_with_png(tmp_path)

    async def scenario(app, pilot):
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "ScreenshotScreen"
        view = screen.query_one("#shot")
        assert view.content_size.height > app.query_one("#wire").content_size.height
        assert "▀" in view.render().plain
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "ScreenshotScreen"
    run_app(snap, scenario, base_dir=tmp_path)


def test_unreadable_png_hint_differs_from_missing(tmp_path):
    snap = views_snapshot()
    save_capture(views_raw(), snap, tmp_path)  # PNG signature only: not decodable

    async def scenario(app, pilot):
        await pilot.press("p")
        assert "cannot read" in app.query_one("#wire").render().plain
    run_app(snap, scenario, base_dir=tmp_path)


def test_screenshot_resized_once_per_pane_size(tmp_path, monkeypatch):
    snap = snapshot_with_png(tmp_path)
    calls = []
    real = tui_app.fit_image
    monkeypatch.setattr(tui_app, "fit_image", lambda *a: calls.append(a[1:]) or real(*a))

    async def scenario(app, pilot):
        await pilot.press("p")
        wire = app.query_one("#wire")
        wire.render()
        wire.render()
        assert len(calls) == 1
    run_app(snap, scenario, base_dir=tmp_path)


def test_checks_table_lists_warnings_first():
    async def scenario(app, pilot):
        severities = [i.severity for i in app.issues]
        assert severities == sorted(severities, key=lambda s: s != "warning")
        table = app.query_one("#issues", DataTable)
        assert str(table.get_row_at(0)[0]) == app.issues[0].severity
    run_app(views_snapshot(), scenario)


def test_x_without_mapping_notifies():
    notes = []

    async def scenario(app, pilot):
        app.notify = lambda message, **kw: notes.append(message)
        await pilot.press("x")
        await pilot.pause()
        assert notes and "no layout XML" in notes[0]
    run_app(views_snapshot(), scenario)


def test_x_shows_every_layout_with_the_id_and_scrolls_to_it(tmp_path):
    snap = views_snapshot()
    filler = "\n".join(f"    <View android:tag=\"{i}\"/>" for i in range(80))
    layouts = {"res/layout/a.xml": f"<FrameLayout>\n{filler}\n    <TextView android:id=\"@id/toolbar\"/>\n</FrameLayout>",
               "res/layout-land/a.xml": '<FrameLayout>\n    <TextView android:id="@id/toolbar"/>\n</FrameLayout>'}
    index = ApkIndex(ids={"toolbar": list(layouts)}, layouts=layouts)
    apply_index(snap.root, index)
    save_capture(views_raw(), snap, tmp_path)
    save_apk_index(index, tmp_path)

    async def scenario(app, pilot):
        await pilot.press("slash", *"toolbar", "enter", "x")
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert "res/layout/a.xml" in screen.body.plain and "res/layout-land/a.xml" in screen.body.plain
        assert screen.query_one("#xml").scroll_y > 0
    run_app(snap, scenario, base_dir=tmp_path)
