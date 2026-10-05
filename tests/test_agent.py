from __future__ import annotations

from helpers import read_fixture

from alayout.agent import _compose_name, parse_agent_dump
from alayout.model import Rect


def _dump():
    return parse_agent_dump(read_fixture("compose_agent.json"))


def _named(dump, rect: Rect):
    return next(hit for hit in dump.hits if hit.bounds == rect)


def test_parse_agent_dump_reads_package_and_window():
    dump = _dump()
    assert dump.package == "com.quitsmoke.tracker"
    assert any("MainActivity" in w for w in dump.windows)
    assert dump.errors == []


def test_named_group_uses_its_own_source_information():
    hit = _named(_dump(), Rect(0, 0, 1080, 2400))
    assert hit.name == "MainScreen"
    assert hit.file == "MainScreen.kt"
    assert hit.line == 89  # the compiler's 0-based line, reported 1-based


def test_app_component_name_climbs_past_generic_building_blocks():
    dump = _dump()
    assert _named(dump, Rect(48, 667, 528, 1197)).name == "Card"  # MoneyCard's box
    assert _named(dump, Rect(711, 884, 873, 1046)).name == "ProgressCircle"  # not the inner Image
    assert _named(dump, Rect(0, 2018, 344, 2258)).name == "NavigationBarItemLayout"


def test_text_leaf_is_reported_as_text():
    assert _named(_dump(), Rect(86, 715, 490, 774)).name == "Text"


def test_compose_name_helper_edge_cases():
    # a parsed C(Name) wins outright
    assert _compose_name(("Box",), ("Dialog", "Dialog.kt", 5)) == "Dialog"
    # all generic -> the nearest composable (the leaf) is kept
    assert _compose_name(("Column", "Layout", "ReusableComposeNode", "Spacer", "Layout",
                          "ReusableComposeNode"), None) == "Spacer"
    # nothing to name
    assert _compose_name((), None) is None
