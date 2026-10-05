from layoutcli.model import Rect, ViewNode
from layoutcli.wireframe import render_wireframe, visible_nodes


def screen_tree(child_visibility="visible"):
    child = ViewNode("a.Box", bounds=Rect(0, 0, 50, 100), visibility=child_visibility)
    root = ViewNode("a.Root", bounds=Rect(0, 0, 100, 200), children=[child])
    return root, child


def lines(text):
    return text.plain.split("\n")


def test_root_box_fills_canvas():
    root, _ = screen_tree()
    out = lines(render_wireframe(root, (100, 200), 10, 10))
    assert len(out) == 10 and all(len(line) == 10 for line in out)
    assert out[0] == "┌───┐────┐"
    assert out[9] == "└────────┘"


def test_selected_box_is_drawn_last_with_label():
    root, child = screen_tree()
    out = lines(render_wireframe(root, (100, 200), 10, 10, selected=child))
    assert out[0] == "┌Box┐────┐"
    assert out[4] == "└───┘    │"


def test_gone_subtree_is_not_drawn():
    root, _ = screen_tree("gone")
    out = lines(render_wireframe(root, (100, 200), 10, 10))
    assert out[4] == "│        │"
    assert [n.short_class for n in visible_nodes(root)] == ["Root"]


def test_offscreen_and_negative_bounds_are_clipped():
    root, _ = screen_tree()
    root.children += [ViewNode("a.Neg", bounds=Rect(-50, -50, 20, 20)),
                      ViewNode("a.Far", bounds=Rect(500, 900, 600, 1000)),
                      ViewNode("a.Zero", bounds=Rect(30, 30, 30, 30))]
    out = lines(render_wireframe(root, (100, 200), 10, 10, selected=root.children[-1]))
    assert len(out) == 10 and all(len(line) == 10 for line in out)


def test_degenerate_canvas_is_empty():
    root, _ = screen_tree()
    assert render_wireframe(root, (100, 200), 0, 10).plain == ""
    assert render_wireframe(root, (0, 0), 10, 10).plain == ""


def test_selected_box_uses_given_label():
    root, child = screen_tree()
    out = lines(render_wireframe(root, (100, 200), 10, 10, selected=child, label="Btn"))
    assert out[0] == "┌Btn┐────┐"
