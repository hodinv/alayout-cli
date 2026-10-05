# LayoutCli Phase 2 (Preview, Search, Checks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add layout checks (CLI `layoutcli check` and TUI badges and list), search and filter in the TUI, and a screenshot preview that can be switched with the wireframe and highlights the selected view.

**Architecture:** Three new pure modules hold the logic, and the TUI only wires them up:
- `checks.py` returns `Issue`s for a `Snapshot`.
- `search.py` matches nodes and computes the subtree to keep when filtering.
- `screenshot.py` renders a PIL image as half-block truecolor `Text`.

`LayoutApp` gains key bindings, an issues table and a search input. The preview widget renders either `render_wireframe` or `render_screenshot`.

**Tech Stack:** Python ≥3.10, textual, rich, typer, Pillow (new), pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-04-layoutcli-design.md` (Phase 2 row). Phase 1 plan: `docs/superpowers/plans/2026-10-04-layoutcli-phase1-core.md`.

## Global Constraints
- Python `>=3.10`. Every module starts with `from __future__ import annotations`.
- All file I/O passes `encoding="utf-8"` explicitly.
- Keys in the TUI:
  - `/` opens search; Enter jumps to the first match.
  - `n` and `N` move to the next and previous match.
  - `f` switches filtering of the tree to matches plus their ancestors.
  - `p` switches the preview between wireframe and screenshot.
  - `c` switches between the checks list and the properties table.
  - `q` quits.
- Checks (spec list):
  - nesting depth > 10
  - touch target < 48dp
  - overlapping clickables
  - off-screen or zero-size views
  - missing content description on clickable or image views
  - INVISIBLE views that still take space
- Commit messages have no attribution trailers (user preference).
- Run tests with `uv run pytest`.

## Review Focus
1. **Compose buttons whose label is a sibling TextView with the same bounds** (seen on the real device: `View` holding a `TextView` and a `Button`). Expect no missing-label warning. The test is in Task 1.
2. **Output redirected to a file on Windows** (`layoutcli check > report.txt`, cp1252 stdout) with `◇`/`⚠` in the labels. It must not crash with UnicodeEncodeError. The test is in Task 2.
3. **Snapshots without a screenshot, or with an unreadable `screen.png`.** The preview should show a hint instead of crashing. The test is in Task 4.
4. **A search with no matches, or `f` before any search.** Expect a notification only, with the tree unchanged. The test is in Task 4.
5. **Screenshot pixel size different from `snapshot.screen`** (for example, a downscaled PNG). The highlight must still land on the right view. The test is in Task 3.

---

### Task 1: Layout checks

**Files:**
- Create: `src/layoutcli/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: `Snapshot`, `ViewNode`, `Rect` (model), `dp` (format)
- Produces:
  - `Issue(check: str, severity: "warning" | "info", node: ViewNode, message: str)`
  - `is_clickable(node) -> bool`
  - `run_checks(snap, max_depth=10) -> list[Issue]`, with check names `deep-nesting`, `touch-target`, `overlapping-clickables`, `off-screen`, `zero-size`, `missing-label` and `invisible-space`

- [ ] **Step 1: Write the failing test**

`tests/test_checks.py`:
```python
from helpers import views_snapshot

from layoutcli.checks import is_clickable, run_checks
from layoutcli.model import Rect, Snapshot, ViewNode

CLICK = {"dumpsys": {"flags": "V.ED..C.. ........"}}


def snap_of(*children, density=420):
    root = ViewNode("a.Root", bounds=Rect(0, 0, 1080, 2400), children=list(children))
    return Snapshot(root=root, screen=(1080, 2400), density=density)


def checks(snap):
    return sorted((i.check, i.node.id or i.node.short_class) for i in run_checks(snap))


def test_is_clickable_reads_uiautomator_or_dumpsys_flags():
    assert is_clickable(ViewNode("a.B", props={"uiautomator": {"clickable": "true"}}))
    assert is_clickable(ViewNode("a.B", props=CLICK))
    assert not is_clickable(ViewNode("a.B", props={"dumpsys": {"flags": "V.ED..... ........"}}))


def test_fixture_snapshot_issues():
    assert checks(views_snapshot()) == [
        ("invisible-space", "progress"),
        ("missing-label", "fab_small"),
        ("touch-target", "fab_small"),
        ("zero-size", "AndroidViewsHandler"),
    ]
    touch = next(i for i in run_checks(views_snapshot()) if i.check == "touch-target")
    assert touch.severity == "warning" and "34x34dp" in touch.message


def test_small_clickable_only_with_density():
    small = ViewNode("a.Btn", id="small", bounds=Rect(0, 0, 100, 100), props=CLICK)
    big = ViewNode("a.Btn", id="big", bounds=Rect(0, 200, 200, 400), props=CLICK)
    assert checks(snap_of(small, big)) == [("touch-target", "small")]
    assert checks(snap_of(ViewNode("a.Btn", bounds=Rect(0, 0, 100, 100), props=CLICK), density=None)) == []


def test_deep_nesting_reported_once_per_branch():
    node = ViewNode("a.Leaf", id="leaf", bounds=Rect(0, 0, 10, 10))
    for i in range(12):
        node = ViewNode("a.Box", id=f"box{i}", bounds=Rect(0, 0, 10, 10), children=[node])
    issues = [i for i in run_checks(snap_of(node)) if i.check == "deep-nesting"]
    assert len(issues) == 1 and issues[0].severity == "info"


def test_overlapping_siblings_but_not_parent_and_child():
    a = ViewNode("a.Btn", id="a", bounds=Rect(0, 0, 500, 500), props=CLICK)
    b = ViewNode("a.Btn", id="b", bounds=Rect(400, 400, 900, 900), props=CLICK)
    inner = ViewNode("a.Btn", id="inner", bounds=Rect(1000, 1000, 1070, 1070), props=CLICK)
    outer = ViewNode("a.Card", id="outer", bounds=Rect(900, 900, 1080, 1080), props=CLICK, children=[inner])
    names = checks(snap_of(a, b, outer))
    assert ("overlapping-clickables", "b") in names
    assert not any(c == "overlapping-clickables" and n in ("inner", "outer") for c, n in names)


def test_off_screen_and_zero_size():
    off = ViewNode("a.V", id="off", bounds=Rect(2000, 0, 2100, 100))
    zero = ViewNode("a.V", id="zero", bounds=Rect(10, 10, 10, 10))
    assert checks(snap_of(off, zero)) == [("off-screen", "off"), ("zero-size", "zero")]


def test_compose_button_labelled_by_sibling_text_with_same_bounds():
    ui = {"clickable": "true", "content-desc": ""}
    button = ViewNode("android.widget.Button", bounds=Rect(48, 1846, 1032, 2016),
                      props={"uiautomator": ui})
    label = ViewNode("android.widget.TextView", text="Продолжить", bounds=Rect(362, 1894, 719, 1968),
                     props={"uiautomator": {"content-desc": ""}})
    holder = ViewNode("android.view.View", bounds=Rect(48, 1846, 1032, 2016),
                      props={"uiautomator": {"clickable": "false"}}, children=[label, button])
    assert checks(snap_of(holder)) == []


def test_unlabelled_image_button_is_reported():
    icon = ViewNode("android.widget.ImageButton", id="icon", bounds=Rect(0, 0, 200, 200),
                    props={"uiautomator": {"clickable": "true", "content-desc": ""}})
    assert checks(snap_of(icon)) == [("missing-label", "icon")]


def test_gone_subtree_is_skipped_and_invisible_reported():
    hidden_child = ViewNode("a.Btn", id="child", bounds=Rect(0, 0, 10, 10), props=CLICK)
    gone = ViewNode("a.Box", id="gone", visibility="gone", bounds=Rect(0, 0, 10, 10), children=[hidden_child])
    invisible = ViewNode("a.Box", id="inv", visibility="invisible", bounds=Rect(0, 0, 300, 300))
    assert checks(snap_of(gone, invisible)) == [("invisible-space", "inv")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_checks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.checks'`

- [ ] **Step 3: Implement `src/layoutcli/checks.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from layoutcli.format import dp
from layoutcli.model import Rect, Snapshot, ViewNode

MAX_DEPTH = 10
MIN_TOUCH_DP = 48


@dataclass(eq=False)
class Issue:
    check: str
    severity: str  # "warning" | "info"
    node: ViewNode
    message: str


def is_clickable(node: ViewNode) -> bool:
    if node.props.get("uiautomator", {}).get("clickable") == "true":
        return True
    flags = node.props.get("dumpsys", {}).get("flags", "")
    return len(flags) > 6 and flags[6] == "C"


def _has_area(r: Rect | None) -> bool:
    return r is not None and r.width > 0 and r.height > 0


def _overlap(a: Rect, b: Rect) -> bool:
    return a.left < b.right and b.left < a.right and a.top < b.bottom and b.top < a.bottom


def _has_text(node: ViewNode) -> bool:
    return any(n.text or n.props.get("uiautomator", {}).get("content-desc") for n, _ in node.walk())


def _name(node: ViewNode) -> str:
    return node.short_class + (f"#{node.id}" if node.id else "")


def run_checks(snap: Snapshot, max_depth: int = MAX_DEPTH) -> list[Issue]:
    issues: list[Issue] = []
    screen = Rect(0, 0, snap.screen[0], snap.screen[1])
    visible: list[tuple[ViewNode, tuple[ViewNode, ...]]] = []

    def walk(node: ViewNode, depth: int, ancestors: tuple[ViewNode, ...], parent_visible: bool) -> None:
        if not parent_visible or node.visibility == "gone":
            return
        if node.visibility == "invisible":
            if _has_area(node.bounds):
                issues.append(Issue("invisible-space", "info", node,
                                    f"INVISIBLE but still takes {node.bounds.width}x{node.bounds.height}px; "
                                    "use GONE if it should not reserve space"))
            return
        visible.append((node, ancestors))
        if depth == max_depth + 1:
            issues.append(Issue("deep-nesting", "info", node,
                                f"nested {depth} levels deep (more than {max_depth}); consider flattening"))
        for child in node.children:
            walk(child, depth + 1, ancestors + (node,), True)

    walk(snap.root, 0, (), True)

    for node, ancestors in visible:
        b = node.bounds
        if b is None:
            continue
        if not _has_area(b):
            if not node.children:
                issues.append(Issue("zero-size", "info", node, "visible but has zero size"))
            continue
        if not _overlap(b, screen):
            issues.append(Issue("off-screen", "warning", node, f"entirely outside the screen {screen}"))
            continue
        clickable = is_clickable(node)
        if clickable and snap.density:
            w, h = dp(b.width, snap.density), dp(b.height, snap.density)
            if w < MIN_TOUCH_DP or h < MIN_TOUCH_DP:
                issues.append(Issue("touch-target", "warning", node,
                                    f"touch target {w}x{h}dp is smaller than {MIN_TOUCH_DP}x{MIN_TOUCH_DP}dp"))
        ui = node.props.get("uiautomator")
        if ui is not None and (clickable or "Image" in node.short_class) and not _has_text(node):
            parent = ancestors[-1] if ancestors else None
            labelled_by_parent = parent is not None and parent.bounds == b and _has_text(parent)
            if not labelled_by_parent:
                issues.append(Issue("missing-label", "warning", node,
                                    "clickable or image view without text or content description"))

    clickables = [(n, a) for n, a in visible
                  if is_clickable(n) and _has_area(n.bounds) and _overlap(n.bounds, screen)]
    for i, (a, a_anc) in enumerate(clickables):
        for b, b_anc in clickables[i + 1:]:
            if a in b_anc or b in a_anc:
                continue
            if _overlap(a.bounds, b.bounds):
                issues.append(Issue("overlapping-clickables", "warning", b,
                                    f"overlaps clickable {_name(a)} at {a.bounds}"))
    return issues
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_checks.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/checks.py tests/test_checks.py
git commit -m "feat: layout checks"
```

---

### Task 2: `layoutcli check` command and safe output encoding

**Files:**
- Modify: `src/layoutcli/cli.py`, `src/layoutcli/format.py` (`node_label(node, warning=False)`)
- Test: `tests/test_cli.py`, `tests/test_format.py`

**Interfaces:**
- Consumes: `run_checks`, `Issue` (Task 1)
- Produces:
  - The CLI command `check [SNAPSHOT_DIR] [-s] [--adb]`. Without a folder it uses the same picker and capture fallback as `inspect`, via the new `_resolve_snapshot(snapshot_dir, adb, serial) -> Path`.
  - `node_label(node, warning: bool = False)` appends ` ⚠` when `warning` is true.
  - The CLI reconfigures stdout and stderr to UTF-8 when they are not a TTY (`_utf8_output()` in the `main` callback).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_format.py`:
```python
def test_node_label_warning_badge():
    node = ViewNode("android.widget.ImageButton", id="fab")
    assert node_label(node, warning=True).plain == "ImageButton #fab ⚠"
```

Append to `tests/test_cli.py`:
```python
import subprocess
import sys


def test_check_prints_issues(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    result = runner.invoke(cli.app, ["check", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "2 warnings, 2 info" in result.output
    assert "touch-target" in result.output and "#fab_small" in result.output


def test_check_output_redirected_to_file_does_not_crash(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "cp1252"
    proc = subprocess.run([sys.executable, "-c", "from layoutcli.cli import app; app()", "check", str(tmp_path)],
                          capture_output=True, env=env)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "⚠" in proc.stdout.decode("utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py tests/test_format.py -q`
Expected: FAIL. `node_label() got an unexpected keyword argument 'warning'`; `No such command 'check'`.

- [ ] **Step 3: Implement**

In `src/layoutcli/format.py`, change the `node_label` signature and append the badge at the end:
```python
def node_label(node: ViewNode, warning: bool = False) -> Text:
    ...existing body...
    if warning:
        label.append(" ⚠", style="yellow")
    return label
```

In `src/layoutcli/cli.py`:
- Add `import sys` and `from layoutcli.checks import run_checks`, plus `from layoutcli.format import node_label`.
- Add a helper and call it first thing in the `main` callback (which typer runs before every command):
```python
def _utf8_output() -> None:
    """Redirected output on Windows defaults to cp1252, which cannot encode ◇/⚠ labels."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
```
- Add the resolver and use it in `inspect`:
```python
def _resolve_snapshot(snapshot_dir: Path | None, adb: str | None, serial: str | None) -> Path:
    directory = snapshot_dir if snapshot_dir is not None else _choose_snapshot()
    return directory if directory is not None else _capture(adb, serial, None)
```
`inspect` body becomes `directory = _resolve_snapshot(snapshot_dir, adb, serial)`, then `snap = load_snapshot(directory)`.
- Add the command:
```python
@app.command()
def check(snapshot_dir: Annotated[Optional[Path], typer.Argument(
              help="Snapshot directory; when omitted, pick a saved one or capture a new one.")] = None,
          serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """Report layout problems: small touch targets, missing labels, overlaps, deep nesting..."""
    try:
        snap = load_snapshot(_resolve_snapshot(snapshot_dir, adb, serial))
    except (AdbError, BuildError, SnapshotError) as e:
        raise _fail(e)
    issues = run_checks(snap)
    warnings = sum(1 for i in issues if i.severity == "warning")
    console.print(f"{warnings} warnings, {len(issues) - warnings} info in "
                  f"{escape(snap.activity or snap.package or 'unknown app')}")
    for issue in sorted(issues, key=lambda i: (i.severity != "warning", i.check)):
        style = "yellow" if issue.severity == "warning" else "dim"
        console.print(f"  [{style}]{issue.severity:<7}[/] {issue.check:<22} "
                      f"{escape(node_label(issue.node, warning=issue.severity == 'warning').plain)}  {escape(issue.message)}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/cli.py src/layoutcli/format.py tests/test_cli.py tests/test_format.py
git commit -m "feat: layoutcli check command; UTF-8 output when redirected"
```

---

### Task 3: Search helpers and screenshot renderer

**Files:**
- Create: `src/layoutcli/search.py`, `src/layoutcli/screenshot.py`
- Modify: `pyproject.toml` (add `"pillow>=10"` to dependencies, then run `uv sync`)
- Test: `tests/test_search.py`, `tests/test_screenshot.py`

**Interfaces:**
- Produces:
  - `node_matches(node, query) -> bool` (case-insensitive substring of class, id, text or content-desc)
  - `find_matches(root, query) -> list[ViewNode]` (pre-order; an empty query gives `[]`)
  - `keep_set(root, query) -> set[ViewNode]` (the matches plus all their ancestors)
  - `render_screenshot(image: PIL.Image.Image, screen: tuple[int, int], cols, rows, selected=None) -> Text`, which draws one `▀` per cell (foreground is the top pixel, background the bottom pixel) and outlines the selected view in `(255, 215, 0)`

- [ ] **Step 1: Write the failing tests**

`tests/test_search.py`:
```python
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
```

`tests/test_screenshot.py`:
```python
from PIL import Image

from layoutcli.model import Rect, ViewNode
from layoutcli.screenshot import render_screenshot


def cell_colors(text, width, row, col):
    style = text.spans[row * width + col].style
    return style.color.triplet, style.bgcolor.triplet


def test_half_blocks_carry_top_and_bottom_pixels():
    img = Image.new("RGB", (4, 4), (255, 0, 0))
    for x in range(4):
        for y in (2, 3):
            img.putpixel((x, y), (0, 0, 255))
    text = render_screenshot(img, (4, 4), 4, 2)
    assert text.plain == "▀▀▀▀\n▀▀▀▀"
    assert cell_colors(text, 4, 0, 0) == ((255, 0, 0), (255, 0, 0))
    assert cell_colors(text, 4, 1, 3) == ((0, 0, 255), (0, 0, 255))


def test_selected_view_is_outlined():
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    text = render_screenshot(img, (10, 10), 10, 5, ViewNode("a.B", bounds=Rect(0, 0, 10, 10)))
    assert cell_colors(text, 10, 0, 5)[0] == (255, 215, 0)
    assert cell_colors(text, 10, 2, 5)[0] == (255, 255, 255)


def test_highlight_maps_screen_coordinates_onto_smaller_image():
    img = Image.new("RGB", (10, 10), (255, 255, 255))  # screenshot downscaled from a 100x100 screen
    text = render_screenshot(img, (100, 100), 10, 5, ViewNode("a.B", bounds=Rect(50, 0, 100, 100)))
    assert cell_colors(text, 10, 2, 5)[0] == (255, 215, 0)
    assert cell_colors(text, 10, 2, 2)[0] == (255, 255, 255)


def test_degenerate_sizes_give_empty_text():
    img = Image.new("RGB", (4, 4))
    assert render_screenshot(img, (4, 4), 0, 5).plain == ""
    assert render_screenshot(img, (4, 4), 5, 0).plain == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_search.py tests/test_screenshot.py -q`
Expected: FAIL with `ModuleNotFoundError` (PIL or `layoutcli.search`)

- [ ] **Step 3: Implement**

Add `"pillow>=10"` to `[project] dependencies` in `pyproject.toml` and run `uv sync`.

`src/layoutcli/search.py`:
```python
from __future__ import annotations

from layoutcli.model import ViewNode


def node_matches(node: ViewNode, query: str) -> bool:
    q = query.casefold()
    fields = (node.class_name, node.id or "", node.text or "",
              node.props.get("uiautomator", {}).get("content-desc", ""))
    return any(q in f.casefold() for f in fields)


def find_matches(root: ViewNode, query: str) -> list[ViewNode]:
    if not query:
        return []
    return [node for node, _ in root.walk() if node_matches(node, query)]


def keep_set(root: ViewNode, query: str) -> set[ViewNode]:
    keep: set[ViewNode] = set()
    if not query:
        return keep

    def visit(node: ViewNode, ancestors: tuple[ViewNode, ...]) -> None:
        if node_matches(node, query):
            keep.add(node)
            keep.update(ancestors)
        for child in node.children:
            visit(child, ancestors + (node,))

    visit(root, ())
    return keep
```

`src/layoutcli/screenshot.py`:
```python
from __future__ import annotations

import math

from PIL import Image, ImageDraw
from rich.color import Color
from rich.style import Style
from rich.text import Text

from layoutcli.model import ViewNode

HIGHLIGHT = (255, 215, 0)


def render_screenshot(image: Image.Image, screen: tuple[int, int], cols: int, rows: int,
                      selected: ViewNode | None = None) -> Text:
    iw, ih = image.size
    if cols <= 0 or rows <= 0 or iw <= 0 or ih <= 0:
        return Text("")
    scale = min(cols / iw, 2 * rows / ih)
    w = max(1, min(cols, round(iw * scale)))
    h = max(2, min(2 * rows, round(ih * scale)))
    h -= h % 2
    small = image.convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
    if selected is not None and selected.bounds is not None and screen[0] > 0 and screen[1] > 0:
        sx, sy = w / screen[0], h / screen[1]
        b = selected.bounds
        x0, y0 = math.floor(b.left * sx), math.floor(b.top * sy)
        x1 = max(x0, math.ceil(b.right * sx) - 1)
        y1 = max(y0, math.ceil(b.bottom * sy) - 1)
        ImageDraw.Draw(small).rectangle([x0, y0, x1, y1], outline=HIGHLIGHT)
    px = small.load()
    text = Text()
    for y in range(0, h, 2):
        if y:
            text.append("\n")
        for x in range(w):
            top, bottom = px[x, y], px[x, y + 1]
            text.append("▀", Style(color=Color.from_rgb(*top), bgcolor=Color.from_rgb(*bottom)))
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_search.py tests/test_screenshot.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/layoutcli/search.py src/layoutcli/screenshot.py tests/test_search.py tests/test_screenshot.py
git commit -m "feat: search helpers and half-block screenshot renderer"
```

---

### Task 4: TUI: preview toggle, search and filter, checks list

**Files:**
- Modify: `src/layoutcli/tui/app.py` (rewrite below), `src/layoutcli/cli.py` (pass `base_dir`), `tests/test_cli.py` (the fakes accept `base_dir`)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `run_checks` (Task 1), `node_label(warning=)` (Task 2), `find_matches`, `keep_set`, `render_screenshot` (Task 3)
- Produces:
  - `LayoutApp(snapshot, base_dir: Path | None = None)`, with the attributes `selected`, `issues` and `_tree_nodes: dict[ViewNode, TreeNode]`
  - `Wireframe(snapshot, image=None)`, with `mode: "wireframe" | "screenshot"` and `toggle_mode()`
  - The CLI calls `LayoutApp(snap, base_dir=directory)`

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, change both fake apps so they accept a `base_dir`. `FakeApp.__init__(self, snapshot, base_dir=None)`, and in `_record_app` and `test_inspect_without_dir_captures_first` use `lambda snap, base_dir=None: ...`.

Append to `tests/test_tui.py`:
```python
from PIL import Image

from layoutcli.checks import run_checks
from layoutcli.snapshot_io import save_capture
from helpers import views_raw


def run_app(snap, scenario, base_dir=None):
    async def main():
        app = LayoutApp(snap, base_dir=base_dir)
        async with app.run_test(size=(140, 45)) as pilot:
            await scenario(app, pilot)
    asyncio.run(main())


def test_preview_toggle_without_screenshot_shows_hint():
    async def scenario(app, pilot):
        await pilot.press("p")
        wire = app.query_one("#wire")
        assert wire.mode == "screenshot"
        assert "no screenshot" in wire.render().plain
        await pilot.press("p")
        assert wire.mode == "wireframe"
    run_app(views_snapshot(), scenario)


def test_preview_renders_real_png(tmp_path):
    raw = views_raw()
    from io import BytesIO
    buf = BytesIO()
    Image.new("RGB", (108, 240), (10, 20, 30)).save(buf, format="PNG")
    raw.screenshot_png = buf.getvalue()
    snap = views_snapshot()
    save_capture(raw, snap, tmp_path)

    async def scenario(app, pilot):
        await pilot.press("p")
        assert "▀" in app.query_one("#wire").render().plain
    run_app(snap, scenario, base_dir=tmp_path)


def test_unreadable_screenshot_shows_hint(tmp_path):
    snap = views_snapshot()
    save_capture(views_raw(), snap, tmp_path)  # helpers PNG_BYTES is a signature only, not a real image

    async def scenario(app, pilot):
        await pilot.press("p")
        assert "no screenshot" in app.query_one("#wire").render().plain
    run_app(snap, scenario, base_dir=tmp_path)


def test_search_jumps_and_cycles_matches():
    async def scenario(app, pilot):
        await pilot.press("slash", *"item_title", "enter")
        await pilot.pause()
        first = app.selected
        assert first.text == "First item"
        await pilot.press("n")
        await pilot.pause()
        assert app.selected.text == "Second item"
        await pilot.press("n")
        await pilot.pause()
        assert app.selected is first
        await pilot.press("N")
        await pilot.pause()
        assert app.selected.text == "Second item"
    run_app(views_snapshot(), scenario)


def test_filter_shows_matches_and_ancestors_only():
    async def scenario(app, pilot):
        tree = app.query_one("#tree", Tree)
        total = sum(1 for _ in tree_nodes(tree.root))
        await pilot.press("f")  # before any search: notification only
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == total
        await pilot.press("slash", *"toolbar", "enter", "f")
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == 5
        await pilot.press("f")
        await pilot.pause()
        assert sum(1 for _ in tree_nodes(tree.root)) == total
    run_app(views_snapshot(), scenario)


def test_search_without_matches_keeps_selection():
    async def scenario(app, pilot):
        before = app.selected
        await pilot.press("slash", *"zzz", "enter")
        await pilot.pause()
        assert app.selected is before
    run_app(views_snapshot(), scenario)


def test_checks_list_and_badges():
    snap = views_snapshot()

    async def scenario(app, pilot):
        await pilot.press("c")
        await pilot.pause()
        issues = app.query_one("#issues", DataTable)
        assert issues.display and not app.query_one("#props").display
        assert issues.row_count == len(run_checks(snap))
        fab = next(n for n in tree_nodes(app.query_one("#tree", Tree).root)
                   if n.data is not None and n.data.id == "fab_small")
        assert "⚠" in str(fab.label)
        await pilot.press("enter")  # select first issue row → jump to its node
        await pilot.pause()
        assert app.selected is app.issues[0].node
    run_app(snap, scenario)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tui.py tests/test_cli.py -q`
Expected: FAIL. `LayoutApp.__init__() got an unexpected keyword argument 'base_dir'`.

- [ ] **Step 3: Implement**

`src/layoutcli/tui/app.py` (full replacement):
```python
from __future__ import annotations

from pathlib import Path

from PIL import Image
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Input, Tree
from textual.widgets.tree import TreeNode

from layoutcli.checks import run_checks
from layoutcli.format import node_label, node_rows
from layoutcli.model import Snapshot, ViewNode
from layoutcli.screenshot import render_screenshot
from layoutcli.search import find_matches, keep_set
from layoutcli.wireframe import render_wireframe


def _load_image(snapshot: Snapshot, base_dir: Path | None) -> Image.Image | None:
    if base_dir is None or not snapshot.screenshot:
        return None
    try:
        with Image.open(base_dir / snapshot.screenshot) as img:
            return img.convert("RGB")
    except OSError:
        return None


class Wireframe(Widget):
    """Preview pane: box wireframe or the device screenshot, with the selected view highlighted."""

    def __init__(self, snapshot: Snapshot, image: Image.Image | None = None, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = snapshot
        self.image = image
        self.selected: ViewNode | None = None
        self.mode = "wireframe"

    def select(self, node: ViewNode | None) -> None:
        self.selected = node
        self.refresh()

    def toggle_mode(self) -> None:
        self.mode = "screenshot" if self.mode == "wireframe" else "wireframe"
        self.refresh()

    def render(self) -> Text:
        size = self.content_size
        if self.mode == "screenshot":
            if self.image is None:
                return Text("no screenshot in this snapshot (p: back to wireframe)", style="dim")
            return render_screenshot(self.image, self.snapshot.screen, size.width, size.height, self.selected)
        return render_wireframe(self.snapshot.root, self.snapshot.screen,
                                size.width, size.height, self.selected)


class LayoutApp(App):
    TITLE = "layoutcli"
    CSS = """
    #tree { width: 1fr; }
    #right { width: 1fr; }
    #props, #issues { height: 2fr; }
    #issues { display: none; }
    #wire { height: 3fr; border: round $primary; }
    #search { dock: bottom; display: none; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("slash", "search", "Search"),
        Binding("n", "next_match", "Next"),
        Binding("N", "prev_match", "Prev"),
        Binding("f", "filter", "Filter"),
        Binding("p", "preview", "Preview"),
        Binding("c", "checks", "Checks"),
        Binding("escape", "close_search", "Close", show=False),
    ]

    def __init__(self, snapshot: Snapshot, base_dir: Path | None = None):
        super().__init__()
        self.snapshot = snapshot
        self.selected: ViewNode | None = None
        self.issues = run_checks(snapshot)
        self._warned = {i.node for i in self.issues if i.severity == "warning"}
        self._tree_nodes: dict[ViewNode, TreeNode[ViewNode]] = {}
        self._image = _load_image(snapshot, base_dir)
        self._query = ""
        self._matches: list[ViewNode] = []
        self._match_index = -1
        self._filtered = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Tree(self._label(self.snapshot.root), data=self.snapshot.root, id="tree")
            with Vertical(id="right"):
                yield DataTable(id="props", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="issues", cursor_type="row", zebra_stripes=True)
                yield Wireframe(self.snapshot, self._image, id="wire")
        yield Input(placeholder="search id, class, text, content-desc  (Enter: find, Esc: close)", id="search")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.snapshot.activity or self.snapshot.package or ""
        self.query_one("#props", DataTable).add_columns("source", "property", "value")
        table = self.query_one("#issues", DataTable)
        table.add_columns("severity", "check", "view", "message")
        for index, issue in enumerate(self.issues):
            table.add_row(Text(issue.severity), Text(issue.check), node_label(issue.node),
                          Text(issue.message), key=str(index))
        self._populate(None)
        self.query_one("#tree", Tree).focus()
        self.select(self.snapshot.root)

    # --- tree -----------------------------------------------------------------------------

    def _label(self, node: ViewNode) -> Text:
        return node_label(node, warning=node in self._warned)

    def _populate(self, keep: set[ViewNode] | None) -> None:
        tree = self.query_one("#tree", Tree)
        tree.clear()
        self._tree_nodes = {self.snapshot.root: tree.root}
        self._add_children(tree.root, self.snapshot.root, keep)
        tree.root.expand()

    def _add_children(self, tree_node: TreeNode[ViewNode], view: ViewNode, keep: set[ViewNode] | None) -> None:
        for child in view.children:
            if keep is not None and child not in keep:
                continue
            if child.children:
                branch = tree_node.add(self._label(child), data=child, expand=True)
                self._tree_nodes[child] = branch
                self._add_children(branch, child, keep)
            else:
                self._tree_nodes[child] = tree_node.add_leaf(self._label(child), data=child)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data is not None:
            self.select(event.node.data)

    def select(self, node: ViewNode) -> None:
        self.selected = node
        table = self.query_one("#props", DataTable)
        table.clear()
        for row in node_rows(node, self.snapshot.density):
            table.add_row(*(Text(cell) for cell in row))  # literal: app strings may contain [markup]
        self.query_one("#wire", Wireframe).select(node)

    def _jump(self, node: ViewNode) -> None:
        tree_node = self._tree_nodes.get(node)
        if tree_node is not None:
            self.query_one("#tree", Tree).move_cursor(tree_node)
        self.select(node)

    # --- search & filter --------------------------------------------------------------------

    def action_search(self) -> None:
        box = self.query_one("#search", Input)
        box.display = True
        box.focus()

    def action_close_search(self) -> None:
        self.query_one("#search", Input).display = False
        self.query_one("#tree", Tree).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._query = event.value.strip()
        self._matches = find_matches(self.snapshot.root, self._query)
        self._match_index = -1
        self.action_close_search()
        if self._filtered:
            self._populate(keep_set(self.snapshot.root, self._query) if self._query else None)
        self._step_match(1)

    def action_next_match(self) -> None:
        self._step_match(1)

    def action_prev_match(self) -> None:
        self._step_match(-1)

    def _step_match(self, step: int) -> None:
        if not self._matches:
            if self._query:
                self.notify(f"no matches for {self._query!r}", severity="warning")
            return
        self._match_index = (self._match_index + step) % len(self._matches)
        self._jump(self._matches[self._match_index])
        self.sub_title = f"{self._query!r}: {self._match_index + 1}/{len(self._matches)}"

    def action_filter(self) -> None:
        if not self._query:
            self.notify("search first (/), then f filters the tree to the matches")
            return
        self._filtered = not self._filtered
        self._populate(keep_set(self.snapshot.root, self._query) if self._filtered else None)
        if self.selected is not None and self.selected in self._tree_nodes:
            self.query_one("#tree", Tree).move_cursor(self._tree_nodes[self.selected])

    # --- preview & checks -------------------------------------------------------------------

    def action_preview(self) -> None:
        self.query_one("#wire", Wireframe).toggle_mode()

    def action_checks(self) -> None:
        issues = self.query_one("#issues", DataTable)
        issues.display = not issues.display
        self.query_one("#props", DataTable).display = not issues.display
        (issues if issues.display else self.query_one("#tree", Tree)).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "issues":
            self._jump(self.issues[int(event.row_key.value)].node)
```

In `src/layoutcli/cli.py`, pass the directory everywhere a `LayoutApp` is created: `LayoutApp(snap, base_dir=directory)` in `inspect`, and `directory = _capture(...)`, then `LayoutApp(load_snapshot(directory), base_dir=directory)` in `main`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/tui/app.py src/layoutcli/cli.py tests/test_tui.py tests/test_cli.py
git commit -m "feat: TUI screenshot preview, search/filter and checks list"
```

---

### Task 5: README and a real-device check

**Files:**
- Modify: `README.md`

- [ ] **Step 1: README.** Add `layoutcli check [DIR]` to the usage list, and replace the TUI keys line with: `arrows: navigate, /: search, n/N: next/prev match, f: filter to matches, p: wireframe/screenshot, c: checks list (Enter jumps), q: quit`.
- [ ] **Step 2: Real device.** Run `uv run layoutcli capture -o layout-snapshots/p2`, then `uv run layoutcli check layout-snapshots/p2`. Run the TUI headless on `p2` (select a node, then render the screenshot preview), as in the Phase 1 end-to-end check. Turn every false positive found into a test plus a fix.
- [ ] **Step 3: Commit.** `git commit -am "docs: Phase 2 usage"`
