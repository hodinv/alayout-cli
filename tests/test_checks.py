from helpers import views_snapshot

from layoutcli.checks import is_clickable, run_checks
from layoutcli.model import Rect, Snapshot, ViewNode

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
