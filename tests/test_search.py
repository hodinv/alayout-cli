from helpers import find_by_id, views_snapshot

from layoutcli.search import find_matches, keep_set, node_matches
from layoutcli.model import ViewNode


def test_node_matches_is_case_insensitive_over_fields():
    node = ViewNode("android.widget.Button", id="ok_button", text="Продолжить",
                    props={"uiautomator": {"content-desc": "Continue"}})
    for query in ("BUTTON", "ok_but", "продолж", "continue"):
        assert node_matches(node, query)
    assert not node_matches(node, "toolbar")


def test_find_matches_in_preorder():
    root = views_snapshot().root
    assert [n.text for n in find_matches(root, "item_title")] == ["First item", "Second item"]
    assert find_matches(root, "") == []
    assert find_matches(root, "no-such-thing") == []


def test_keep_set_contains_matches_and_ancestors():
    root = views_snapshot().root
    keep = keep_set(root, "toolbar")
    toolbar = find_by_id(root, "toolbar")
    assert toolbar in keep and root in keep
    assert [n.class_name for n in keep if n.children and n is not root].count("android.widget.LinearLayout") == 1
    assert len(keep) == 5
    assert keep_set(root, "") == set()


def test_aliases_are_searchable():
    node = ViewNode("android.view.View", text=None)
    root = ViewNode("a.Root", children=[node])
    assert find_matches(root, "button") == []
    assert find_matches(root, "button", aliases={node: 'Button "OK"'}) == [node]
    assert node in keep_set(root, "button", aliases={node: 'Button "OK"'})
