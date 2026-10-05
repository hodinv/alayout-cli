import json

import pytest

from alayout.model import Rect, Snapshot, ViewNode, short_id


def test_rect_size_offset_and_str():
    r = Rect(10, 20, 110, 70)
    assert r.size == (100, 50)
    assert r.offset(5, -5) == Rect(15, 15, 115, 65)
    assert str(r) == "[10,20][110,70]"
    assert Rect.from_list(r.to_list()) == r


def test_short_id():
    assert short_id("com.example:id/title") == "title"
    assert short_id("app:id/list") == "list"
    assert short_id("") is None
    assert short_id(None) is None


def test_walk_yields_nodes_with_depth():
    leaf = ViewNode("android.widget.TextView")
    root = ViewNode("android.widget.FrameLayout", children=[ViewNode("a.B", children=[leaf])])
    assert [(n.short_class, d) for n, d in root.walk()] == [
        ("FrameLayout", 0), ("B", 1), ("TextView", 2)]


def test_snapshot_round_trips_through_json():
    root = ViewNode(
        "DecorView", bounds=Rect(0, 0, 1080, 2400), sources=["dumpsys"],
        props={"dumpsys": {"hash": "abc"}},
        children=[ViewNode("android.widget.TextView", id="title", text="Привет 👋", visibility="gone")])
    snap = Snapshot(root=root, screen=(1080, 2400), density=420, package="com.example",
                    activity="com.example.Main", device={"serial": "emu"},
                    captured_at="2026-10-04T10:00:00+00:00", capabilities={"dumpsys": "ok"},
                    screenshot="screen.png")
    data = snap.to_dict()
    assert data["format"] == 1
    back = Snapshot.from_dict(json.loads(json.dumps(data)))
    assert back.to_dict() == data
    assert back.screen == (1080, 2400)
    assert back.root.children[0].text == "Привет 👋"


def test_snapshot_from_dict_rejects_unknown_format():
    with pytest.raises(ValueError, match="format"):
        Snapshot.from_dict({"format": 99})
