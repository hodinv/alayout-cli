from layoutcli.compose import Component, infer_components
from layoutcli.model import Rect, ViewNode


def u(cls, bounds, text=None, children=(), **attrs):
    props = {"clickable": "false", "checkable": "false", "checked": "false", "selected": "false",
             "enabled": "true", "scrollable": "false", "content-desc": ""}
    props.update(attrs)
    return ViewNode(f"android.{cls}", text=text, bounds=Rect(*bounds), sources=["uiautomator"],
                    props={"uiautomator": props}, children=list(children))


def row(i, label):
    top = 711 + i * 144
    return u("view.View", (48, top, 1032, top + 144), clickable="true", children=[
        u("view.View", (24, top, 168, top + 144), clickable="true"),
        u("widget.TextView", (192, top + 43, 346, top + 102), text=label)])


def screen(*extra):
    rows = [row(i, label) for i, label in enumerate(["Всегда", "Часто", "Периодически", "Редко", "Никогда"])]
    button = u("view.View", (48, 1846, 1032, 2016), clickable="true", children=[
        u("widget.TextView", (362, 1894, 719, 1968), text="Продолжить"),
        u("widget.Button", (48, 1846, 1032, 2016))])
    back = u("widget.ImageView", (0, 79, 108, 223), clickable="true", **{"content-desc": "Back"})
    title = u("widget.TextView", (48, 1503, 249, 1562), text="Вопрос 5")
    content = u("view.View", (0, 0, 1080, 2167), children=[*rows, button, back, title, *extra])
    host = ViewNode("androidx.compose.ui.platform.AndroidComposeView", bounds=Rect(0, 0, 1080, 2400),
                    sources=["dumpsys", "uiautomator"], props={"uiautomator": {"clickable": "false"}},
                    children=[content])
    outside = u("widget.Button", (0, 0, 10, 10), clickable="true", text="Native")
    root = ViewNode("DecorView", bounds=Rect(0, 0, 1080, 2400),
                    children=[ViewNode("android.widget.FrameLayout", id="content", children=[outside, host])])
    return root, rows, button, back, title, outside


def test_describe_format():
    assert Component("Button", "OK").describe() == 'Button "OK"'
    assert Component("Toggle", "Wi-Fi", ("checked", "disabled"), (2, 3)).describe() == \
        'Toggle "Wi-Fi" [checked, disabled] (2 of 3 similar)'
    assert Component("Selector").describe() == "Selector"


def test_real_screen_patterns():
    root, rows, button, back, title, outside = screen()
    found = infer_components(root)
    assert found[button] == Component("Button", "Продолжить")
    assert button.children[1] not in found  # the Button role marker is folded into its parent
    assert found[back] == Component("IconButton", "Back")
    assert [found[r] for r in rows] == [Component("Clickable", label, (), (i + 1, 5)) for i, label in
                                        enumerate(["Всегда", "Часто", "Периодически", "Редко", "Никогда"])]
    assert all(found[r.children[0]] == Component("Selector") for r in rows)
    assert title not in found and outside not in found and root not in found


def test_toggles_text_fields_scrollables_and_states():
    toggle = u("view.View", (0, 0, 500, 100), clickable="true", checkable="true", checked="false",
               children=[u("widget.TextView", (10, 10, 300, 90), text="Notifications")])
    box = u("widget.CheckBox", (0, 100, 100, 200), clickable="true", checkable="true", checked="true",
            **{"content-desc": "Agree"})
    field = u("widget.EditText", (0, 200, 500, 300), clickable="true", text="Email")
    scroller = u("view.View", (0, 300, 1080, 2000), scrollable="true")
    disabled = u("view.View", (0, 2000, 500, 2100), clickable="true", enabled="false", children=[
        u("widget.TextView", (10, 2010, 200, 2090), text="Send"), u("widget.Button", (0, 2000, 500, 2100))])
    root, *_ = screen(toggle, box, field, scroller, disabled)
    found = infer_components(root)
    assert found[toggle] == Component("Toggle", "Notifications", ("unchecked",))
    assert found[box] == Component("CheckBox", "Agree", ("checked",))
    assert found[field] == Component("TextField", "Email")
    assert found[scroller] == Component("Scrollable")
    # same structure as "Продолжить" under the same parent: likely the same composable
    assert found[disabled] == Component("Button", "Send", ("disabled",), (2, 2))


def test_interop_views_under_android_views_handler_are_not_compose():
    native = u("widget.Button", (0, 0, 100, 100), clickable="true", text="Interop")
    handler = ViewNode("androidx.compose.ui.platform.AndroidViewsHandler", children=[native])
    host = ViewNode("androidx.compose.ui.platform.AndroidComposeView", children=[handler])
    assert infer_components(ViewNode("DecorView", children=[host])) == {}


def test_uiautomator_only_tree_uses_compose_view_class():
    clickable = u("view.View", (0, 0, 500, 100), clickable="true",
                  children=[u("widget.TextView", (0, 0, 100, 50), text="Go")])
    host = u("view.View", (0, 0, 1080, 2167), children=[clickable])
    compose_view = ViewNode("androidx.compose.ui.platform.ComposeView", children=[host])
    assert infer_components(ViewNode("android.widget.FrameLayout", children=[compose_view])) == {
        clickable: Component("Clickable", "Go")}


def test_compose_nodes_region():
    from layoutcli.compose import compose_nodes
    root, rows, button, back, title, outside = screen()
    region = compose_nodes(root)
    assert button in region and button.children[1] in region and title in region
    assert outside not in region and root not in region
    host = next(n for n, _ in root.walk() if n.class_name.endswith("AndroidComposeView"))
    assert host not in region
