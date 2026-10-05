from layoutcli.merge import merge
from layoutcli.model import Rect, ViewNode
from layoutcli.parse.dumpsys import DNode, DumpsysResult

VISIBLE = "V.E...... ........"


def _d(cls, rel, res_id=None, children=(), flags=VISIBLE):
    return DNode(cls, str(id(rel)), flags, rel, res_id, list(children))


def _u(cls, bounds, id=None, text=None, children=()):
    return ViewNode(cls, id=id, bounds=bounds, text=text, sources=["uiautomator"],
                    props={"uiautomator": {"package": "p"}}, children=list(children))


def _by_id(root, view_id):
    return next(n for n, _ in root.walk() if n.id == view_id)


def test_scrollview_content_and_clipped_rows_get_scrolled_positions():
    # ScrollView at y=100..1100 scrolled by 300: the content starts at y=-200
    rows = [_d("a.Row", Rect(0, 400 * i, 1000, 400 * (i + 1)), f"app:id/r{i}") for i in range(4)]
    hidden = _d("a.Badge", Rect(0, 400, 100, 500), "app:id/badge", flags="I.E...... ........")
    content = _d("a.LinearLayout", Rect(0, 0, 1000, 1600), "app:id/content", rows + [hidden])
    scroll = _d("a.ScrollView", Rect(0, 100, 1000, 1100), "app:id/scroll", [content])
    droot = DNode("DecorView", "1", children=[scroll])
    u_rows = [_u("a.Row", Rect(0, 100, 1000, 200), "r0"), _u("a.Row", Rect(0, 200, 1000, 600), "r1"),
              _u("a.Row", Rect(0, 600, 1000, 1000), "r2"), _u("a.Row", Rect(0, 1000, 1000, 1100), "r3")]
    u_root = _u("android.widget.FrameLayout", Rect(0, 0, 1000, 1100), children=[
        _u("a.ScrollView", Rect(0, 100, 1000, 1100), "scroll", children=[
            _u("a.LinearLayout", Rect(0, 100, 1000, 1100), "content", children=u_rows)])])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    assert _by_id(merged, "content").bounds == Rect(0, -200, 1000, 1400)
    assert _by_id(merged, "r0").bounds == Rect(0, -200, 1000, 200)  # clipped at the top
    assert _by_id(merged, "r1").bounds == Rect(0, 200, 1000, 600)
    assert _by_id(merged, "r3").bounds == Rect(0, 1000, 1000, 1400)  # clipped at the bottom
    assert _by_id(merged, "badge").bounds == Rect(0, 200, 100, 300)  # dumpsys-only view follows the scroll


def test_clipped_view_without_matched_children_is_placed_by_its_visible_edge():
    inner = _d("a.Text", Rect(0, 0, 1000, 100))
    content = _d("a.LinearLayout", Rect(0, 0, 1000, 1600), "app:id/content", [inner])
    scroll = _d("a.ScrollView", Rect(0, 100, 1000, 1100), "app:id/scroll", [content])
    droot = DNode("DecorView", "1", children=[scroll])
    u_root = _u("android.widget.FrameLayout", Rect(0, 0, 1000, 1100), children=[
        _u("a.ScrollView", Rect(0, 100, 1000, 1100), "scroll", children=[
            _u("a.LinearLayout", Rect(0, 100, 1000, 700), "content")])])  # bottom edge visible at 700
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    assert _by_id(merged, "content").bounds == Rect(0, -900, 1000, 700)
    assert _by_id(merged, "content").children[0].bounds == Rect(0, -900, 1000, -800)


def test_interop_views_inside_compose_appear_once():
    text = _d("android.widget.TextView", Rect(0, 0, 1000, 100), "app:id/interop_text")
    holder = _d("androidx.compose.ui.viewinterop.ViewFactoryHolder", Rect(0, 500, 1000, 600), children=[text])
    handler = _d("androidx.compose.ui.platform.AndroidViewsHandler", Rect(0, 0, 1000, 1000), children=[holder])
    acv = _d("androidx.compose.ui.platform.AndroidComposeView", Rect(0, 0, 1000, 1000), children=[handler])
    host = _d("androidx.compose.ui.platform.ComposeView", Rect(0, 0, 1000, 1000), "app:id/host", [acv])
    droot = DNode("DecorView", "1", children=[host])
    u_text = _u("android.widget.TextView", Rect(0, 500, 1000, 600), "interop_text", text="From a View")
    u_root = _u("android.widget.FrameLayout", Rect(0, 0, 1000, 1000), children=[
        _u("android.view.ViewGroup", Rect(0, 0, 1000, 1000), "host", children=[
            _u("android.view.View", Rect(0, 0, 1000, 1000), children=[
                _u("android.widget.TextView", Rect(0, 0, 1000, 100), text="Compose title"), u_text])])])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    found = [n for n, _ in merged.walk() if n.id == "interop_text"]
    assert len(found) == 1
    assert found[0].sources == ["dumpsys", "uiautomator"] and found[0].text == "From a View"
    assert any(n.text == "Compose title" for n, _ in merged.walk())


def test_view_reported_with_empty_bounds_keeps_dumpsys_position():
    content = _d("a.LinearLayout", Rect(0, 0, 1000, 900), "app:id/content")
    navbar = _d("android.view.View", Rect(0, 900, 1000, 1000), "android:id/navigationBarBackground")
    droot = DNode("DecorView", "1", children=[content, navbar])
    u_root = _u("android.widget.FrameLayout", Rect(0, 0, 1000, 900), children=[
        _u("a.LinearLayout", Rect(0, 0, 1000, 900), "content"),
        _u("android.view.View", Rect(0, 0, 0, 0), "navigationBarBackground")])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    assert _by_id(merged, "navigationBarBackground").bounds == Rect(0, 900, 1000, 1000)
