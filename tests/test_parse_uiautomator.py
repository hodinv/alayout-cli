import xml.etree.ElementTree as ET

import pytest
from helpers import find_by_id, read_fixture

from layoutcli.model import Rect
from layoutcli.parse.uiautomator import parse_bounds, parse_uiautomator

HEADER = "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"


def test_parses_fixture_tree():
    roots = parse_uiautomator(read_fixture("views_uiautomator.xml"))
    assert len(roots) == 1
    root = roots[0]
    assert root.class_name == "android.widget.FrameLayout"
    assert root.bounds == Rect(0, 0, 1080, 2400)
    assert root.sources == ["uiautomator"]
    assert root.props["uiautomator"]["package"] == "com.example.demo"
    toolbar = find_by_id(root, "toolbar")
    assert toolbar.bounds == Rect(0, 63, 1080, 210)
    assert toolbar.children[0].text == "Demo"
    assert toolbar.children[0].id is None


def test_unicode_text_and_entities():
    xml = (HEADER + '<hierarchy rotation="0"><node index="0" text="Привет &amp; 👋" resource-id="" '
           'class="android.widget.TextView" package="p" content-desc="" bounds="[0,0][10,10]" /></hierarchy>')
    (node,) = parse_uiautomator(xml)
    assert node.text == "Привет & 👋"


def test_multiple_windows_give_multiple_roots():
    xml = (HEADER + '<hierarchy rotation="0">'
           '<node class="a.A" package="p1" bounds="[0,0][1,1]" />'
           '<node class="b.B" package="p2" bounds="[0,0][2,2]" /></hierarchy>')
    assert [r.props["uiautomator"]["package"] for r in parse_uiautomator(xml)] == ["p1", "p2"]


def test_bad_xml_raises_parse_error():
    with pytest.raises(ET.ParseError):
        parse_uiautomator("<hierarchy><node")


def test_parse_bounds():
    assert parse_bounds("[-5,0][100,50]") == Rect(-5, 0, 100, 50)
    assert parse_bounds("") is None
