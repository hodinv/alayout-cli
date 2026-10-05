import copy

from helpers import find_by_id, views_snapshot

from alayout.diff import diff_snapshots, node_paths
from alayout.model import Rect, ViewNode


def test_paths_use_ids_classes_and_sibling_indexes():
    paths = node_paths(views_snapshot().root)
    assert "/" in paths
    assert "/LinearLayout/#content/#root/#toolbar" in paths
    assert "/LinearLayout/#content/#root/#list/#item_title" in paths
    assert "/LinearLayout/#content/#root/#list/#item_title[2]" in paths


def test_identical_snapshots_have_no_diff():
    assert diff_snapshots(views_snapshot().root, views_snapshot().root).empty


def test_changed_fields_are_reported():
    a, b = views_snapshot().root, views_snapshot().root
    title = find_by_id(b, "toolbar").children[0]
    title.text = "Settings"
    find_by_id(b, "toolbar").bounds = Rect(0, 63, 1080, 250)
    result = diff_snapshots(a, b)
    by_path = {c.path: c.fields for c in result.changed}
    assert by_path["/LinearLayout/#content/#root/#toolbar"] == {
        "bounds": ("[0,63][1080,210]", "[0,63][1080,250]")}
    assert by_path["/LinearLayout/#content/#root/#toolbar/AppCompatTextView"] == {"text": ("Demo", "Settings")}
    assert not result.added and not result.removed


def test_only_topmost_added_and_removed_nodes_are_listed():
    a, b = views_snapshot().root, views_snapshot().root
    host = find_by_id(b, "compose_host")
    find_by_id(b, "root").children.remove(host)
    extra = ViewNode("a.Banner", id="banner", children=[ViewNode("a.Text", text="Hi")])
    find_by_id(b, "root").children.append(extra)
    result = diff_snapshots(a, b)
    assert [p for p, _ in result.removed] == ["/LinearLayout/#content/#root/#compose_host"]
    assert [p for p, _ in result.added] == ["/LinearLayout/#content/#root/#banner"]


def test_props_present_on_one_side_only_are_ignored_and_root_class_does_not_matter():
    a = ViewNode("DecorView", bounds=Rect(0, 0, 10, 10), props={"dumpsys": {"hash": "1"}},
                 children=[ViewNode("a.B", id="x", props={"uiautomator": {"clickable": "true"}})])
    b = ViewNode("android.widget.FrameLayout", bounds=Rect(0, 0, 10, 10),
                 children=[ViewNode("a.B", id="x", props={})])
    result = diff_snapshots(a, b)
    assert [c.fields for c in result.changed] == [{"class": ("DecorView", "android.widget.FrameLayout")}]


def test_compared_props_detect_state_changes():
    a, b = views_snapshot().root, views_snapshot().root
    find_by_id(b, "fab_small").props["uiautomator"]["enabled"] = "false"
    (change,) = diff_snapshots(a, b).changed
    assert change.fields == {"enabled": ("true", "false")}


def test_first_row_keeps_its_path_when_a_second_row_appears():
    a = ViewNode("a.List", id="list", children=[ViewNode("a.Row", id="row", text="one")])
    b = ViewNode("a.List", id="list", children=[ViewNode("a.Row", id="row", text="uno"),
                                                ViewNode("a.Row", id="row", text="two")])
    result = diff_snapshots(a, b)
    assert [p for p, _ in result.added] == ["/#row[2]"]
    assert not result.removed
    assert [(c.path, c.fields) for c in result.changed] == [("/#row", {"text": ("one", "uno")})]


def test_class_comparison_can_be_disabled():
    a = ViewNode("a.Root", children=[ViewNode("androidx.appcompat.widget.AppCompatTextView", id="t")])
    b = ViewNode("a.Root", children=[ViewNode("android.widget.TextView", id="t")])
    assert not diff_snapshots(a, b).empty
    assert diff_snapshots(a, b, compare_class=False).empty
