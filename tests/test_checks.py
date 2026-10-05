from helpers import views_snapshot

from alayout.checks import is_clickable, run_checks
from alayout.model import Rect, Snapshot, ViewNode

CLICK = {"dumpsys": {"flags": "V.ED..C.. ........"}}


def snap_of(*children, density=420):
    root = ViewNode("a.Root", bounds=Rect(0, 0, 1080, 2400), children=list(children))
    return Snapshot(root=root, screen=(1080, 2400), density=density)


def checks(snap):
    return sorted((i.check, i.node.id or i.node.short_class) for i in run_checks(snap))


def test_is_clickable_reads_uiautomator_or_dumpsys_flags():
    assert is_clickable(ViewNode("a.B", props={"uiautomator": {"clickable": "true"}}))
    assert is_clickable(ViewNode("a.B", props=CLICK))
    assert not is_clickable(ViewNode("a.B", props={"dumpsys": {"flags": "V.ED..... ........"}}))


def test_fixture_snapshot_issues():
    assert checks(views_snapshot()) == [
        ("invisible-space", "progress"),
        ("missing-label", "fab_small"),
        ("touch-target", "fab_small"),
        ("zero-size", "AndroidViewsHandler"),
    ]
    touch = next(i for i in run_checks(views_snapshot()) if i.check == "touch-target")
    assert touch.severity == "warning" and "34x34dp" in touch.message


def test_small_clickable_only_with_density():
    small = ViewNode("a.Btn", id="small", bounds=Rect(0, 0, 100, 100), props=CLICK)
    big = ViewNode("a.Btn", id="big", bounds=Rect(0, 200, 200, 400), props=CLICK)
    assert checks(snap_of(small, big)) == [("touch-target", "small")]
    assert checks(snap_of(ViewNode("a.Btn", bounds=Rect(0, 0, 100, 100), props=CLICK), density=None)) == []


def test_deep_nesting_reported_once_per_branch():
    node = ViewNode("a.Leaf", id="leaf", bounds=Rect(0, 0, 10, 10))
    for i in range(12):
        node = ViewNode("a.Box", id=f"box{i}", bounds=Rect(0, 0, 10, 10), children=[node])
    issues = [i for i in run_checks(snap_of(node)) if i.check == "deep-nesting"]
    assert len(issues) == 1 and issues[0].severity == "info"


def test_overlapping_siblings_but_not_parent_and_child():
    a = ViewNode("a.Btn", id="a", bounds=Rect(0, 0, 500, 500), props=CLICK)
    b = ViewNode("a.Btn", id="b", bounds=Rect(400, 400, 900, 900), props=CLICK)
    inner = ViewNode("a.Btn", id="inner", bounds=Rect(1000, 1000, 1070, 1070), props=CLICK)
    outer = ViewNode("a.Card", id="outer", bounds=Rect(900, 900, 1080, 1080), props=CLICK, children=[inner])
    names = checks(snap_of(a, b, outer))
    assert ("overlapping-clickables", "b") in names
    assert not any(c == "overlapping-clickables" and n in ("inner", "outer") for c, n in names)


def test_off_screen_and_zero_size():
    off = ViewNode("a.V", id="off", bounds=Rect(2000, 0, 2100, 100))
    zero = ViewNode("a.V", id="zero", bounds=Rect(10, 10, 10, 10))
    assert checks(snap_of(off, zero)) == [("off-screen", "off"), ("zero-size", "zero")]


def test_compose_button_labelled_by_sibling_text_with_same_bounds():
    ui = {"clickable": "true", "content-desc": ""}
    button = ViewNode("android.widget.Button", bounds=Rect(48, 1846, 1032, 2016),
                      props={"uiautomator": ui})
    label = ViewNode("android.widget.TextView", text="Продолжить", bounds=Rect(362, 1894, 719, 1968),
                     props={"uiautomator": {"content-desc": ""}})
    holder = ViewNode("android.view.View", bounds=Rect(48, 1846, 1032, 2016),
                      props={"uiautomator": {"clickable": "false"}}, children=[label, button])
    assert checks(snap_of(holder)) == []


def test_unlabelled_image_button_is_reported():
    icon = ViewNode("android.widget.ImageButton", id="icon", bounds=Rect(0, 0, 200, 200),
                    props={"uiautomator": {"clickable": "true", "content-desc": ""}})
    assert checks(snap_of(icon)) == [("missing-label", "icon")]


def test_gone_subtree_is_skipped_and_invisible_reported():
    hidden_child = ViewNode("a.Btn", id="child", bounds=Rect(0, 0, 10, 10), props=CLICK)
    gone = ViewNode("a.Box", id="gone", visibility="gone", bounds=Rect(0, 0, 10, 10), children=[hidden_child])
    invisible = ViewNode("a.Box", id="inv", visibility="invisible", bounds=Rect(0, 0, 300, 300))
    assert checks(snap_of(gone, invisible)) == [("invisible-space", "inv")]


def test_touch_target_skipped_for_view_clipped_by_uiautomator_window():
    # edge-to-edge app: uiautomator clips the window at 2167, the real button is taller
    clipped = ViewNode("android.view.View", id="clipped", bounds=Rect(48, 2040, 1032, 2167), sources=["uiautomator"],
                       props={"uiautomator": {"clickable": "true", "content-desc": "Later"}})
    short = ViewNode("android.view.View", id="short", bounds=Rect(48, 1000, 1032, 1127),
                     props={"uiautomator": {"clickable": "true", "content-desc": "Short"}})
    snap = snap_of(clipped, short, density=480)
    snap.root.props = {"uiautomator": {"bounds": "[0,0][1080,2167]"}}
    assert checks(snap) == [("touch-target", "short")]


def test_unlabelled_clickable_inside_labelled_clickable_row_is_nested_not_missing_label():
    ui = {"clickable": "true", "content-desc": ""}
    radio = ViewNode("android.view.View", id="radio", bounds=Rect(24, 711, 168, 855), props={"uiautomator": ui})
    text = ViewNode("android.widget.TextView", text="Всегда", bounds=Rect(192, 754, 346, 813),
                    props={"uiautomator": {"content-desc": ""}})
    row = ViewNode("android.view.View", id="row", bounds=Rect(48, 711, 1032, 855),
                   props={"uiautomator": ui}, children=[radio, text])
    issues = run_checks(snap_of(row, density=480))
    assert [(i.check, i.node.id, i.severity) for i in issues] == [("nested-clickable", "radio", "info")]
    assert "onClick = null" in issues[0].message


def test_button_labelled_by_same_bounds_sibling_inside_taller_parent():
    ui = {"clickable": "true", "content-desc": ""}
    button = ViewNode("android.widget.Button", id="btn", bounds=Rect(48, 1846, 1032, 2016), props={"uiautomator": ui})
    label = ViewNode("android.widget.TextView", text="OK", bounds=Rect(48, 1846, 1032, 2016),
                     props={"uiautomator": {"content-desc": ""}})
    column = ViewNode("android.view.View", bounds=Rect(0, 1500, 1080, 2100), children=[label, button],
                      props={"uiautomator": {}})
    assert checks(snap_of(column)) == []


def test_icon_inside_labelled_clickable_row_is_not_reported():
    icon = ViewNode("android.widget.ImageView", id="icon", bounds=Rect(0, 0, 144, 144),
                    props={"uiautomator": {"clickable": "false", "content-desc": ""}})
    text = ViewNode("android.widget.TextView", text="Settings", bounds=Rect(160, 40, 600, 100),
                    props={"uiautomator": {"content-desc": ""}})
    row = ViewNode("android.widget.LinearLayout", id="row", bounds=Rect(0, 0, 1080, 144),
                   props={"uiautomator": {"clickable": "true", "content-desc": ""}}, children=[icon, text])
    assert checks(snap_of(row)) == []


def test_off_screen_subtree_reported_once():
    page = ViewNode("a.Page", id="page", bounds=Rect(1080, 0, 2160, 2400),
                    children=[ViewNode("a.Text", id=f"t{i}", bounds=Rect(1100, i * 100, 2000, i * 100 + 90))
                              for i in range(5)])
    assert checks(snap_of(page)) == [("off-screen", "page")]


def test_clipping_rule_only_applies_to_uiautomator_only_nodes():
    # a merged view's bounds come from dumpsys, so they are real even at the window edge
    merged = ViewNode("a.NavItem", id="nav", bounds=Rect(0, 2100, 100, 2167), sources=["dumpsys", "uiautomator"],
                      props={"uiautomator": {"clickable": "true", "content-desc": "Home"}})
    snap = snap_of(merged, density=480)
    snap.root.props = {"uiautomator": {"bounds": "[0,0][1080,2167]"}}
    assert checks(snap) == [("touch-target", "nav")]
