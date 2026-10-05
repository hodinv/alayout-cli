from alayout.format import dp, node_label, node_rows, size_text
from alayout.model import Rect, ViewNode


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


def test_node_label_with_compose_component():
    from alayout.compose import Component
    node = ViewNode("android.view.View", bounds=Rect(48, 1846, 1032, 2016), sources=["uiautomator"])
    label = node_label(node, warning=True, component=Component("Button", "Продолжить"))
    assert label.plain == 'View 984x170 ◇ ⟨Button "Продолжить"⟩ ⚠'


def test_node_rows_include_compose_section():
    from alayout.compose import Component
    node = ViewNode("android.view.View", sources=["uiautomator"], props={"uiautomator": {"clickable": "true"}})
    rows = node_rows(node, 420, Component("Clickable", "Всегда", ("selected",), (1, 5)))
    i = rows.index(("view", "sources", "uiautomator"))
    assert rows[i + 1:i + 5] == [("compose", "component", "Clickable"), ("compose", "label", "Всегда"),
                                 ("compose", "state", "selected"), ("compose", "similar", "1 of 5")]
    assert rows[-1] == ("uiautomator", "clickable", "true")


def test_compose_label_uses_component_kind_instead_of_class():
    from alayout.compose import Component
    node = ViewNode("android.view.View", bounds=Rect(48, 1846, 1032, 2016), sources=["uiautomator"])
    assert node_label(node, component=Component("Button", "Продолжить", (), (1, 2)), in_compose=True).plain == \
        'Button "Продолжить" 984x170 (1 of 2 similar)'
    assert node_label(node, component=Component("Selector"), in_compose=True, warning=True).plain == \
        "Selector 984x170 ⚠"


def test_compose_label_shows_the_real_call_chain_when_named():
    node = ViewNode("android.view.View", bounds=Rect(0, 211, 984, 270), sources=["uiautomator"],
                    props={"uiautomator": {},
                           "compose": {"name": "RadioItem",
                                       "path": "QuestionContent > RadioGroup > RadioItem"}})
    assert node_label(node, in_compose=True).plain == \
        "QuestionContent > RadioGroup > RadioItem 984x59"


def test_compose_label_for_plain_semantics_nodes():
    text = ViewNode("android.widget.TextView", text="Всегда", bounds=Rect(0, 0, 154, 59), sources=["uiautomator"])
    image = ViewNode("android.widget.ImageView", bounds=Rect(0, 0, 10, 10), sources=["uiautomator"],
                     props={"uiautomator": {"content-desc": "Logo"}})
    marker = ViewNode("android.widget.Button", bounds=Rect(0, 0, 984, 170), sources=["uiautomator"])
    group = ViewNode("android.view.View", bounds=Rect(0, 0, 1080, 2167), sources=["uiautomator"])
    assert node_label(text, in_compose=True).plain == 'Text "Всегда" 154x59'
    assert node_label(image, in_compose=True).plain == 'Image "Logo" 10x10'
    assert node_label(marker, in_compose=True).plain == "Button role 984x170"
    assert node_label(group, in_compose=True).plain == "Group 1080x2167"
