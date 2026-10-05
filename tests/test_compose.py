from __future__ import annotations

from alayout.agent import ComposeHit
from alayout.compose import apply_compose_names
from alayout.model import Rect, ViewNode

BOX = Rect(48, 667, 528, 1197)


def _snapshot():
    """A Compose host with one semantics node (what uiautomator reports) at BOX."""
    leaf = ViewNode("android.view.View", bounds=BOX, props={"uiautomator": {}})
    host = ViewNode("androidx.compose.ui.platform.AndroidComposeView",
                    bounds=Rect(0, 0, 1080, 2400), children=[leaf])
    root = ViewNode("com.android.internal.policy.DecorView",
                    bounds=Rect(0, 0, 1080, 2400), children=[host])
    return root, leaf


def test_apply_compose_names_writes_name_file_and_path():
    root, leaf = _snapshot()
    hits = [ComposeHit(bounds=BOX, name="Card", file="Card.kt", line=72,
                       path=("MoneyCard", "Card", "Surface"))]
    assert apply_compose_names(root, hits) == 1
    compose = leaf.props["compose"]
    assert compose["name"] == "Card"
    assert compose["file"] == "Card.kt"
    assert compose["path"] == "MoneyCard > Card > Surface"


def test_call_chain_drops_plumbing_and_collapses_repeats():
    root, leaf = _snapshot()
    path = ("QuestionWithSelectionScreen", "ScaffoldScreen", "Layout", "ReusableComposeNode",
            "AdaptiveFitLayout", "CompositionLocalProvider", "QuestionContent", "Column", "Column",
            "RadioGroup", "RadioItem", "Text", "BasicText", "Layout", "ReusableComposeNode")
    apply_compose_names(root, [ComposeHit(bounds=BOX, name="RadioItem", file=None, line=None, path=path)])
    assert leaf.props["compose"]["path"] == (
        "QuestionWithSelectionScreen > ScaffoldScreen > AdaptiveFitLayout > QuestionContent > "
        "Column > RadioGroup > RadioItem > Text")


def test_exact_bounds_prefer_a_name_the_app_wrote_over_a_generic_one():
    root, leaf = _snapshot()
    hits = [ComposeHit(bounds=BOX, name="Box", file=None, line=None, path=("Box",)),
            ComposeHit(bounds=BOX, name="MoneyCard", file=None, line=None, path=("MoneyCard",))]
    apply_compose_names(root, hits)
    assert leaf.props["compose"]["name"] == "MoneyCard"


def test_name_attaches_by_overlap_when_bounds_are_not_exact():
    root, leaf = _snapshot()
    nearly = Rect(BOX.left, BOX.top, BOX.right, BOX.bottom - 2)  # ~99% of the box
    hit = ComposeHit(bounds=nearly, name="Card", file=None, line=None, path=("Card",))
    assert apply_compose_names(root, [hit]) == 1
    assert leaf.props["compose"]["name"] == "Card"


def test_zero_sized_hits_are_ignored():
    root, leaf = _snapshot()
    hit = ComposeHit(bounds=Rect(0, 0, 0, 0), name="Ghost", file=None, line=None)
    assert apply_compose_names(root, [hit]) == 0
    assert "compose" not in leaf.props
