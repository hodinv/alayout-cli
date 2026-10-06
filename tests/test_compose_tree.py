from __future__ import annotations

import json

from alayout.agent import ComposeHit, parse_agent_dump
from alayout.compose import build_compose_subtree, compose_nodes, graft_compose_tree
from alayout.model import Rect, ViewNode

# Two radio items that share one RadioGroup, which shares one QuestionContent.
A = Rect(0, 100, 600, 160)
B = Rect(0, 160, 600, 220)


def _hit(bounds, path, ids, **kw):
    return ComposeHit(bounds=bounds, name=path[-1], file=kw.get("file"), line=kw.get("line"),
                      path=path, path_ids=ids)


def _radio_hits():
    return [
        _hit(A, ("QuestionContent", "Column", "RadioGroup", "RadioItem", "Text"), (10, 11, 12, 13, 14)),
        _hit(B, ("QuestionContent", "Column", "RadioGroup", "RadioItem", "Text"), (10, 11, 12, 15, 16)),
    ]


def test_shared_group_id_nests_items_under_one_parent():
    roots = build_compose_subtree(_radio_hits())
    assert [n.short_class for n in roots] == ["QuestionContent"]
    question = roots[0]
    column = question.children[0]
    group = column.children[0]
    assert group.short_class == "RadioGroup"
    assert [n.short_class for n in group.children] == ["RadioItem", "RadioItem"]  # one group, two items
    first_item = group.children[0]
    assert [n.short_class for n in first_item.children] == ["Text"]


def test_internal_node_bounds_are_the_union_of_their_leaves():
    roots = build_compose_subtree(_radio_hits())
    group = roots[0].children[0].children[0]
    assert group.bounds == Rect(0, 100, 600, 220)  # spans both items
    assert group.children[0].bounds == A
    assert group.children[1].bounds == B


def test_structural_plumbing_is_dropped_and_repeats_collapsed():
    hit = _hit(A, ("QuestionContent", "Layout", "ReusableComposeNode", "Column", "Column", "Text"),
               (1, 2, 3, 4, 5, 6))
    roots = build_compose_subtree([hit])
    # Layout/ReusableComposeNode are plumbing; the two Columns collapse to one
    chain = []
    node = roots[0]
    while True:
        chain.append(node.short_class)
        if not node.children:
            break
        node = node.children[0]
    assert chain == ["QuestionContent", "Column", "Text"]


def test_leaves_are_enriched_from_uiautomator_semantics_by_bounds():
    semantics = [ViewNode("android.view.View", bounds=A, text="Always",
                          sources=["uiautomator"], props={"uiautomator": {"selected": "true"}})]
    roots = build_compose_subtree(_radio_hits(), semantics)
    text_a = roots[0].children[0].children[0].children[0].children[0]
    assert text_a.short_class == "Text" and text_a.bounds == A
    assert text_a.text == "Always"
    assert text_a.props["uiautomator"]["selected"] == "true"
    assert "uiautomator" in text_a.sources


def test_group_id_is_recorded_on_each_node_as_a_ref():
    group = build_compose_subtree(_radio_hits())[0].children[0].children[0]
    assert group.props["compose"]["id"] == "12"  # RadioGroup#12, the same in both items


def test_unmatched_uiautomator_semantics_are_not_added_under_the_host():
    # a semantics node whose box no composable drew must not appear: the hierarchy is compose's
    orphan = ViewNode("android.view.View", bounds=Rect(0, 900, 100, 950), text="stray",
                      sources=["uiautomator"], props={"uiautomator": {}})
    roots = build_compose_subtree(_radio_hits(), [orphan])
    assert orphan not in roots
    assert all(n.short_class == "QuestionContent" for n in roots)


def test_lookahead_duplicate_pass_is_merged_keeping_the_one_with_text():
    # the same composable composed twice at the same box (lookahead), different ids, only one placed
    lookahead = _hit(A, ("Screen", "Card", "Text"), (1, 2, 3))
    real = _hit(A, ("Screen", "Card", "Text"), (1, 9, 7))
    semantics = [ViewNode("android.view.View", bounds=A, text="Hello",
                          sources=["uiautomator"], props={"uiautomator": {}})]
    roots = build_compose_subtree([lookahead, real], semantics)
    assert len(roots) == 1 and roots[0].short_class == "Screen"
    card = roots[0].children[0]
    assert [n.short_class for n in card.children] == ["Text"]  # one Text, not two
    assert card.children[0].text == "Hello"


def test_duplicate_pass_with_no_onscreen_leaves_is_flagged_ghost():
    # two QuestionContent roots (an AnimatedContent transition), same name, different ids, different
    # bounds (so not merged); only one has a uiautomator-backed leaf
    live = _hit(Rect(0, 211, 984, 307), ("QuestionContent", "RadioItem", "Text"), (195, 300, 301))
    ghost = _hit(Rect(0, 211, 984, 400), ("QuestionContent", "RadioItem", "Text"), (290, 448, 467))
    semantics = [ViewNode("android.view.View", bounds=Rect(0, 211, 984, 307),
                          text="Always", sources=["uiautomator"], props={"uiautomator": {}})]
    roots = {r.props["compose"]["id"]: r for r in build_compose_subtree([live, ghost], semantics)}
    assert roots["195"].props["compose"].get("ghost") is None  # on screen
    assert roots["290"].props["compose"].get("ghost") == "true"  # the empty duplicate


def test_lone_semantics_less_composable_is_not_a_ghost():
    # CustomRadioButtonBox draws no semantics, but it is the only one of its name -> not a ghost
    hit = _hit(Rect(0, 211, 96, 307), ("RadioItem", "CustomRadioButtonBox", "Box"), (448, 455, 461))
    label = _hit(Rect(0, 211, 189, 270), ("RadioItem", "Text"), (448, 467))
    semantics = [ViewNode("android.view.View", bounds=Rect(0, 211, 189, 270),
                          text="Always", sources=["uiautomator"], props={"uiautomator": {}})]
    root = build_compose_subtree([hit, label], semantics)[0]
    custom = next(n for n, _ in root.walk() if n.short_class == "CustomRadioButtonBox")
    assert custom.props["compose"].get("ghost") is None


def _host_tree(hits_bounds):
    semantics = [ViewNode("android.view.View", bounds=b, sources=["uiautomator"],
                          props={"uiautomator": {}}) for b in hits_bounds]
    host = ViewNode("androidx.compose.ui.platform.AndroidComposeView",
                    bounds=Rect(0, 0, 1080, 2400), children=semantics)
    root = ViewNode("com.android.internal.policy.DecorView",
                    bounds=Rect(0, 0, 1080, 2400), children=[host])
    return root, host


def test_graft_replaces_semantics_children_with_the_compose_tree():
    root, host = _host_tree([A, B])
    created = graft_compose_tree(root, _radio_hits())
    assert created and created > 0
    assert [n.short_class for n in host.children] == ["QuestionContent"]
    assert host.children[0] in compose_nodes(root)  # internal nodes render in the compose view


def test_without_ids_the_tree_is_left_to_the_overlay_path():
    root, host = _host_tree([A])
    no_ids = [ComposeHit(bounds=A, name="Text", file=None, line=None, path=("Text",), path_ids=())]
    assert graft_compose_tree(root, no_ids) is None  # host untouched; apply_compose_names handles it


def test_compose_mode_drops_native_interop_views_under_the_host():
    # in --compose mode the host shows the composable tree only, not hosted Android Views
    root, host = _host_tree([A, B])
    interop = ViewNode("android.widget.Button", bounds=A, sources=["dumpsys", "uiautomator"],
                       props={"dumpsys": {"hash": "x"}})
    host.children.append(interop)
    graft_compose_tree(root, _radio_hits())
    assert interop not in [n for n, _ in root.walk()]  # the native view is gone
    assert [n.short_class for n in host.children] == ["QuestionContent"]


def test_subcomposition_nests_under_its_host_when_the_path_is_prefixed():
    # what the agent emits once collectSubcomposition threads the Scaffold host's path/ids:
    # Scaffold's content (a subcomposition) carries the full ScaffoldScreen > Scaffold prefix, so it
    # nests under Scaffold instead of starting its own branch
    scaffold = ComposeHit(bounds=Rect(0, 0, 1080, 2400), name="Scaffold", file=None, line=None,
                          path=("ScaffoldScreen", "Scaffold"), path_ids=(5, 6))
    content = ComposeHit(bounds=Rect(0, 211, 984, 307), name="Text", file=None, line=None,
                         path=("ScaffoldScreen", "Scaffold", "QuestionContent", "Text"),
                         path_ids=(5, 6, 40, 41))
    roots = build_compose_subtree([scaffold, content])
    assert [n.short_class for n in roots] == ["ScaffoldScreen"]
    chain = []
    node = roots[0]
    while node.children:
        node = node.children[0]
        chain.append(node.short_class)
    assert chain == ["Scaffold", "QuestionContent", "Text"]  # not a sibling of Scaffold


def _ghost_and_live_hits():
    live = ComposeHit(bounds=Rect(0, 211, 984, 307), name="Text", file=None, line=None,
                      path=("QuestionContent", "RadioItem", "Text"), path_ids=(195, 300, 301))
    ghost = ComposeHit(bounds=Rect(0, 211, 984, 400), name="Text", file=None, line=None,
                       path=("QuestionContent", "RadioItem", "Text"), path_ids=(290, 448, 467))
    return live, ghost


def test_graft_prunes_ghosts_by_default():
    live, ghost = _ghost_and_live_hits()
    root, host = _host_tree([Rect(0, 211, 984, 307)])  # only the live one is on screen
    graft_compose_tree(root, [live, ghost])
    ids = {n.props["compose"]["id"] for n, _ in root.walk() if "compose" in n.props}
    assert "195" in ids and "290" not in ids  # the ghost QuestionContent is gone


def test_graft_keeps_ghosts_with_show_ghosts():
    live, ghost = _ghost_and_live_hits()
    root, host = _host_tree([Rect(0, 211, 984, 307)])
    graft_compose_tree(root, [live, ghost], show_ghosts=True)
    ghost_node = next(n for n, _ in root.walk()
                      if n.props.get("compose", {}).get("id") == "290")
    assert ghost_node.props["compose"]["ghost"] == "true"  # kept, and still marked


def test_agent_text_is_used_when_present():
    # the agent read the string off the text node's modifier; it should land on the Text leaf even
    # without any uiautomator semantics to match against
    hit = ComposeHit(bounds=A, name="Text", file=None, line=None,
                     path=("Card", "Text"), path_ids=(1, 2), text="Front Door")
    roots = build_compose_subtree([hit])
    text_node = roots[0].children[0]
    assert text_node.short_class == "Text"
    assert text_node.text == "Front Door"


def test_overlapping_hosts_do_not_repeat_the_tree():
    # two AndroidComposeViews covering the same area (nested/stacked). The tree must appear once,
    # under the innermost host; the outer host keeps its own children (not a duplicate tree).
    inner = ViewNode("androidx.compose.ui.platform.AndroidComposeView", bounds=Rect(0, 0, 800, 600),
                     children=[ViewNode("android.view.View", bounds=b, sources=["uiautomator"],
                                        props={"uiautomator": {}}) for b in (A, B)])
    outer = ViewNode("androidx.compose.ui.platform.AndroidComposeView", bounds=Rect(0, 0, 1080, 2400),
                     children=[inner])
    root = ViewNode("com.android.internal.policy.DecorView", bounds=Rect(0, 0, 1080, 2400),
                    children=[outer])
    graft_compose_tree(root, _radio_hits())
    tops = [n for n, _ in root.walk() if n.props.get("compose", {}).get("name") == "QuestionContent"]
    assert len(tops) == 1  # exactly one tree, not one per host
    assert tops[0] in inner.children  # placed under the innermost host


def test_zero_area_host_does_not_swallow_hits():
    # a degenerate [0,98][0,98] ComposeView must not win assignment over the real one just by having
    # the smallest area; the tree belongs to the host that actually covers the content
    real = ViewNode("androidx.compose.ui.platform.AndroidComposeView", bounds=Rect(0, 0, 800, 600),
                    children=[ViewNode("android.view.View", bounds=b, sources=["uiautomator"],
                                       props={"uiautomator": {}}) for b in (A, B)])
    ghost_host = ViewNode("androidx.compose.ui.platform.AndroidComposeView", bounds=Rect(0, 98, 0, 98))
    root = ViewNode("com.android.internal.policy.DecorView", bounds=Rect(0, 0, 1080, 2400),
                    children=[ghost_host, real])
    graft_compose_tree(root, _radio_hits())
    assert [n.short_class for n in real.children] == ["QuestionContent"]  # content on the real host
    assert ghost_host.children == []  # the zero-size host got nothing


def test_parse_then_graft_builds_the_tree_from_agent_json():
    doc = {
        "agent": 1, "request": 1, "package": "demo", "tooling": True,
        "windows": [{"activity": "demo.Main", "left": 0, "top": 0, "composeViews": [{
            "view": "androidx.compose.ui.platform.AndroidComposeView",
            "bounds": [0, 0, 600, 300],
            "nodes": [
                {"bounds": [0, 100, 600, 160],
                 "path": ["RadioGroup", "RadioItem", "Text"], "pathIds": [11, 12, 13]},
                {"bounds": [0, 160, 600, 220],
                 "path": ["RadioGroup", "RadioItem", "Text"], "pathIds": [11, 14, 15]},
            ]}]}],
        "errors": [],
    }
    dump = parse_agent_dump(json.dumps(doc))
    assert dump.hits[0].path_ids == (11, 12, 13)
    root, host = _host_tree([Rect(0, 100, 600, 160), Rect(0, 160, 600, 220)])
    graft_compose_tree(root, dump.hits)
    group = host.children[0]
    assert group.short_class == "RadioGroup"
    assert [n.short_class for n in group.children] == ["RadioItem", "RadioItem"]
