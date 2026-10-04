from helpers import read_fixture

from layoutcli.model import Rect
from layoutcli.parse.dumpsys import parse_dumpsys


def block(component, resumed, hierarchy):
    lines = [f"  ACTIVITY {component} 1a2b pid=1",
             "    Local Activity 3c4d State:",
             f"      mResumed={'true' if resumed else 'false'} mStopped=false",
             "    View Hierarchy:"]
    lines += ["      " + h for h in hierarchy]
    return "\n".join(lines) + "\n"


def all_nodes(node):
    yield node
    for c in node.children:
        yield from all_nodes(c)


def test_parses_fixture_hierarchy():
    r = parse_dumpsys(read_fixture("views_dumpsys.txt"))
    assert r.package == "com.example.demo"
    assert r.activity == "com.example.demo.MainActivity"
    assert r.root.class_name == "DecorView" and r.root.rel is None
    (linear,) = r.root.children
    assert linear.class_name == "android.widget.LinearLayout"
    stub, content = linear.children
    assert stub.visibility == "gone"
    assert stub.res_id == "android:id/action_mode_bar_stub"
    assert content.rel == Rect(0, 63, 1080, 2400)
    assert len(list(all_nodes(r.root))) == 15


def test_flags_ids_and_aid_suffix():
    r = parse_dumpsys(read_fixture("views_dumpsys.txt"))
    nodes = {n.hash: n for n in all_nodes(r.root)}
    assert nodes["bbbbbbb"].visibility == "invisible"
    assert nodes["8888888"].clickable is True
    assert nodes["8888888"].res_id == "app:id/item_title"
    assert nodes["6666666"].res_id is None and nodes["6666666"].clickable is False


def test_negative_bounds_and_id_without_name():
    text = block("p/.A", True, [
        "DecorView@1[A]",
        "  android.widget.HorizontalScrollView{1a V.E...... ........ -40,0-1120,200}",
        "    android.view.View{2b V.ED..... ........ 0,0-0,0 #7f0a0999}",
    ])
    r = parse_dumpsys(text)
    scroll = r.root.children[0]
    assert scroll.rel == Rect(-40, 0, 1120, 200)
    assert scroll.children[0].res_id is None
    assert scroll.children[0].rel == Rect(0, 0, 0, 0)


def test_prefers_resumed_activity_over_later_ones():
    text = (block("com.a/.Resumed", True, ["DecorView@1[Resumed]"]) +
            block("com.b/com.b.Paused", False, ["DecorView@2[Paused]"]))
    r = parse_dumpsys(text)
    assert (r.package, r.activity) == ("com.a", "com.a.Resumed")


def test_falls_back_to_last_activity_with_hierarchy():
    text = (block("com.a/.One", False, ["DecorView@1[One]"]) +
            block("com.b/.Two", False, ["DecorView@2[Two]"]))
    assert parse_dumpsys(text).activity == "com.b.Two"


def test_returns_none_without_view_hierarchy():
    assert parse_dumpsys("TASK 1 id=1\n  ACTIVITY a/.B 1 pid=2\n") is None
    assert parse_dumpsys("") is None
