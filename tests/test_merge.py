import pytest
from helpers import find_by_id, read_fixture

from alayout.merge import MergeError, merge, pick_window
from alayout.model import Rect, ViewNode
from alayout.parse.dumpsys import DNode, DumpsysResult, parse_dumpsys
from alayout.parse.uiautomator import parse_uiautomator


def fixture_merge():
    return merge(parse_dumpsys(read_fixture("views_dumpsys.txt")),
                 parse_uiautomator(read_fixture("views_uiautomator.xml")))


def test_fixture_merge_uses_real_classes_and_absolute_bounds():
    root = fixture_merge()
    assert root.class_name == "DecorView"
    assert root.bounds == Rect(0, 0, 1080, 2400)
    toolbar = find_by_id(root, "toolbar")
    assert toolbar.class_name == "com.google.android.material.appbar.MaterialToolbar"
    assert toolbar.bounds == Rect(0, 63, 1080, 210)
    assert toolbar.sources == ["dumpsys", "uiautomator"]
    assert toolbar.props["dumpsys"]["hash"] == "5555555"
    assert toolbar.props["uiautomator"]["class"] == "android.view.ViewGroup"
    assert toolbar.children[0].text == "Demo"


def test_hidden_views_come_from_dumpsys_only():
    root = fixture_merge()
    progress = find_by_id(root, "progress")
    assert progress.visibility == "invisible"
    assert progress.sources == ["dumpsys"]
    assert progress.bounds == Rect(500, 1763, 580, 1843)
    assert find_by_id(root, "action_mode_bar_stub").visibility == "gone"


def test_repeated_ids_match_in_order():
    items = [n for n, _ in fixture_merge().walk() if n.id == "item_title"]
    assert [i.text for i in items] == ["First item", "Second item"]
    assert items[1].bounds == Rect(0, 336, 1080, 462)


def test_compose_semantics_nodes_hang_under_compose_view():
    root = fixture_merge()
    host = find_by_id(root, "compose_host").children[0]
    assert host.class_name == "androidx.compose.ui.platform.AndroidComposeView"
    assert [c.class_name for c in host.children] == [
        "androidx.compose.ui.platform.AndroidViewsHandler",
        "android.widget.TextView", "android.widget.Button"]
    assert host.children[1].text == "Hello Compose"
    ui_only = [n for n, _ in root.walk() if n.sources == ["uiautomator"]]
    assert len(ui_only) == 3
    assert sum(1 for _ in root.walk()) == 18


def test_scrolled_children_take_uiautomator_position_and_propagate():
    leaf = DNode("android.widget.ImageView", "4", "V.ED..... ........", Rect(10, 10, 20, 20))
    item = DNode("android.widget.LinearLayout", "3", "V.E...... ........", Rect(0, 100, 100, 150), children=[leaf])
    lst = DNode("androidx.recyclerview.widget.RecyclerView", "2", "VFED..... ........",
                Rect(0, 0, 100, 200), "app:id/list", [item])
    droot = DNode("DecorView", "1", children=[lst])
    u_item = ViewNode("android.widget.LinearLayout", bounds=Rect(0, 80, 100, 130),
                      sources=["uiautomator"], props={"uiautomator": {}})
    u_list = ViewNode("androidx.recyclerview.widget.RecyclerView", id="list", bounds=Rect(0, 0, 100, 200),
                      sources=["uiautomator"], props={"uiautomator": {}}, children=[u_item])
    u_root = ViewNode("android.widget.FrameLayout", bounds=Rect(0, 0, 100, 200),
                      sources=["uiautomator"], props={"uiautomator": {"package": "p"}}, children=[u_list])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    m_item = merged.children[0].children[0]
    assert m_item.bounds == Rect(0, 80, 100, 130)
    assert m_item.children[0].bounds == Rect(10, 90, 20, 100)


def test_dumpsys_only_assumes_origin_zero():
    root = merge(parse_dumpsys(read_fixture("views_dumpsys.txt")), [])
    assert find_by_id(root, "toolbar").bounds == Rect(0, 63, 1080, 210)
    assert root.bounds == Rect(0, 0, 1080, 2400)
    assert all(n.sources == ["dumpsys"] for n, _ in root.walk())


def test_uiautomator_only_returns_window_for_package():
    a = ViewNode("a.A", props={"uiautomator": {"package": "com.ime"}})
    b = ViewNode("b.B", props={"uiautomator": {"package": "com.app"}})
    assert pick_window([a, b], "com.app") is b
    assert pick_window([a, b], None) is a
    assert merge(None, [a, b]) is a


def test_nothing_to_merge_raises():
    with pytest.raises(MergeError):
        merge(None, [])


def _u(cls, bounds, id=None, text=None, children=()):
    return ViewNode(cls, id=id, bounds=bounds, text=text, sources=["uiautomator"],
                    props={"uiautomator": {"package": "p"}}, children=list(children))


def test_children_match_regardless_of_uiautomator_order():
    toolbar = DNode("a.Toolbar", "2", "V.E...... ........", Rect(0, 0, 100, 20), "app:id/toolbar")
    lst = DNode("a.List", "3", "V.E...... ........", Rect(0, 20, 100, 200), "app:id/list")
    droot = DNode("DecorView", "1", children=[lst, toolbar])  # dumpsys: child index order
    u_root = _u("android.widget.FrameLayout", Rect(0, 0, 100, 200), children=[  # uiautomator: sorted by position
        _u("android.view.ViewGroup", Rect(0, 0, 100, 20), id="toolbar"),
        _u("android.view.ViewGroup", Rect(0, 20, 100, 200), id="list")])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    assert [c.id for c in merged.children] == ["list", "toolbar"]
    assert all(c.sources == ["dumpsys", "uiautomator"] for c in merged.children)


def test_views_without_ids_match_by_position():
    top = DNode("a.Text", "2", "V.E...... ........", Rect(0, 0, 100, 20))
    bottom = DNode("a.Text", "3", "V.E...... ........", Rect(0, 100, 100, 120))
    droot = DNode("DecorView", "1", children=[bottom, top])
    u_root = _u("android.widget.FrameLayout", Rect(0, 0, 100, 120), children=[
        _u("android.widget.TextView", Rect(0, 0, 100, 20), text="top"),
        _u("android.widget.TextView", Rect(0, 100, 100, 120), text="bottom")])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    assert [c.text for c in merged.children] == ["bottom", "top"]


def test_pick_window_rejects_foreign_package():
    other = ViewNode("a.A", props={"uiautomator": {"package": "com.android.permissioncontroller"}})
    assert pick_window([other], "com.app") is None


def test_root_keeps_full_size_when_uiautomator_window_is_clipped():
    content = DNode("android.widget.LinearLayout", "2", "V.E...... ........", Rect(0, 0, 100, 200))
    droot = DNode("DecorView", "1", children=[content])
    u_root = _u("android.widget.FrameLayout", Rect(0, 0, 100, 180),
                children=[_u("android.widget.LinearLayout", Rect(0, 0, 100, 180))])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    assert merged.bounds == Rect(0, 0, 100, 200)
    assert merged.children[0].bounds == Rect(0, 0, 100, 200)
