from layoutcli.format import dp, node_label, node_rows, size_text
from layoutcli.model import Rect, ViewNode


def test_dp_and_size_text():
    assert dp(147, 420) == 56
    assert dp(147, None) is None
    assert size_text(Rect(0, 63, 1080, 210), 420) == "1080x147px (411x56dp)"
    assert size_text(Rect(0, 0, 10, 20), None) == "10x20px"


def test_node_label_shows_id_text_size_and_state():
    node = ViewNode("androidx.appcompat.widget.AppCompatTextView", id="title", text="Hello",
                    bounds=Rect(0, 0, 100, 40), visibility="gone", sources=["dumpsys"])
    assert node_label(node).plain == 'AppCompatTextView #title "Hello" 100x40 [gone]'


def test_node_label_marks_semantics_only_nodes():
    node = ViewNode("android.widget.Button", sources=["uiautomator"])
    assert node_label(node).plain == "Button ◇"


def test_node_rows_lists_view_fields_then_source_props():
    node = ViewNode("a.B", id="x", bounds=Rect(0, 0, 420, 420), sources=["dumpsys", "uiautomator"],
                    props={"uiautomator": {"clickable": "true"}, "dumpsys": {"hash": "abc"}})
    rows = node_rows(node, 420)
    assert rows[0] == ("view", "class", "a.B")
    assert ("view", "size", "420x420px (160x160dp)") in rows
    assert rows[-2:] == [("dumpsys", "hash", "abc"), ("uiautomator", "clickable", "true")]


def test_node_label_warning_badge():
    node = ViewNode("android.widget.ImageButton", id="fab")
    assert node_label(node, warning=True).plain == "ImageButton #fab ⚠"
