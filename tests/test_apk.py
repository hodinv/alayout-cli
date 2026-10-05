import shutil
from pathlib import Path

import pytest
from helpers import FIXTURES, find_by_id, read_fixture, views_snapshot

from layoutcli.apk import (ApkError, ApkIndex, apply_index, build_index, find_aapt2, parse_resources_dump,
                           parse_xmltree)
from layoutcli.model import ViewNode

AAPT2 = "aapt2.exe" if __import__("os").name == "nt" else "aapt2"


def make_build_tools(sdk: Path, *versions: str) -> dict[str, Path]:
    made = {}
    for v in versions:
        d = sdk / "build-tools" / v
        d.mkdir(parents=True)
        (d / AAPT2).write_bytes(b"")
        made[v] = d / AAPT2
    return made


def test_find_aapt2_prefers_highest_version_next_to_adb(tmp_path):
    made = make_build_tools(tmp_path / "sdk", "34.0.0", "36.0.0", "9.0.0")
    adb = tmp_path / "sdk" / "platform-tools" / "adb.exe"
    assert find_aapt2(adb, env={}) == made["36.0.0"]


def test_find_aapt2_missing_raises(tmp_path):
    with pytest.raises(ApkError, match="aapt2 not found"):
        find_aapt2(tmp_path / "nosdk" / "platform-tools" / "adb.exe", env={})


def test_parse_resources_dump():
    names, layouts = parse_resources_dump(read_fixture("aapt2_resources.txt"))
    assert names["0x7f020003"] == "id/toolbar"
    assert names["0x7f010000"] == "dimen/screen_padding"
    assert layouts == ["res/layout/activity_main.xml", "res/layout-v1/activity_main.xml",
                       "res/layout/item_row.xml", "res/layout-v1/item_row.xml"]


def test_parse_xmltree_reconstructs_readable_xml():
    names, _ = parse_resources_dump(read_fixture("aapt2_resources.txt"))
    xml, ids = parse_xmltree(read_fixture("aapt2_xmltree_activity_main.txt"), names)
    assert ids == ["root", "toolbar", "fab_small"]
    assert xml.splitlines() == [
        '<LinearLayout android:orientation="1" android:id="@id/root" android:padding="@dimen/screen_padding" '
        'android:layout_width="match_parent" android:layout_height="match_parent">',
        '    <TextView android:id="@id/toolbar" android:layout_width="match_parent" '
        'android:layout_height="56dp" android:text="Demo"/>',
        '    <ImageButton android:id="@id/fab_small" android:layout_width="34dp" android:layout_height="34dp"/>',
        "</LinearLayout>",
    ]


def test_index_round_trip_restrict_and_apply():
    index = ApkIndex(ids={"toolbar": ["res/layout/activity_main.xml"], "other": ["res/layout/x.xml"]},
                     layouts={"res/layout/activity_main.xml": "<A/>", "res/layout/x.xml": "<X/>"})
    small = index.restricted_to({"toolbar", "fab_small"})
    assert small.to_dict() == {"ids": {"toolbar": ["res/layout/activity_main.xml"]},
                               "layouts": {"res/layout/activity_main.xml": "<A/>"}}
    assert ApkIndex.from_dict(small.to_dict()).to_dict() == small.to_dict()
    snap = views_snapshot()
    assert apply_index(snap.root, index) == 1
    assert find_by_id(snap.root, "toolbar").props["apk"] == {"layouts": "res/layout/activity_main.xml"}


def test_build_index_with_fake_runner_prefers_files_with_attributes():
    resources = read_fixture("aapt2_resources.txt")
    tree = read_fixture("aapt2_xmltree_activity_main.txt")
    bare = "N: android=http://schemas.android.com/apk/res/android (line=2)\n  E: LinearLayout (line=2)\n"

    def runner(args):
        if args[2] == "resources":
            return resources
        file = args[args.index("--file") + 1]
        if file.endswith("activity_main.xml"):
            return tree if "layout-v1" in file else bare
        return bare.replace("LinearLayout", "TextView")

    index = build_index(Path("x.apk"), Path("aapt2"), runner=runner)
    assert index.ids["toolbar"] == ["res/layout-v1/activity_main.xml"]
    assert "android:id=\"@id/toolbar\"" in index.layouts["res/layout-v1/activity_main.xml"]


@pytest.mark.skipif(not shutil.which("aapt2") and not list(
    (Path(__import__("os").environ.get("LOCALAPPDATA", "/nonexistent")) / "Android" / "Sdk" / "build-tools").glob("*/aapt2*")),
    reason="aapt2 not installed")
def test_build_index_with_real_aapt2():
    index = build_index(FIXTURES / "mini.apk", find_aapt2(None))
    assert set(index.ids) == {"root", "toolbar", "fab_small", "item_title"}
    assert index.ids["item_title"] == ["res/layout-v1/item_row.xml"]


def test_framework_ids_are_not_mapped_to_app_layouts():
    index = ApkIndex(ids={"content": ["res/layout/abc_popup_menu_item_layout.xml"],
                          "toolbar": ["res/layout/activity_main.xml"]}, layouts={})
    framework = ViewNode("android.widget.FrameLayout", id="content",
                         props={"dumpsys": {"resource_id": "android:id/content"}})
    from_ui = ViewNode("android.widget.FrameLayout", id="content",
                       props={"uiautomator": {"resource-id": "android:id/content"}})
    app_view = ViewNode("a.Toolbar", id="toolbar", props={"dumpsys": {"resource_id": "app:id/toolbar"}})
    root = ViewNode("DecorView", children=[framework, from_ui, app_view])
    assert apply_index(root, index) == 1
    assert "apk" not in framework.props and "apk" not in from_ui.props
    assert app_view.props["apk"] == {"layouts": "res/layout/activity_main.xml"}
