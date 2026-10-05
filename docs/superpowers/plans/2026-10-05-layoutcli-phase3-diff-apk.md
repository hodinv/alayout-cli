# LayoutCli Phase 3 (Snapshot Diff, APK Decoding) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- `layoutcli diff [A] [B]` reports added, removed and changed views between two snapshots.
- `--apk PATH|device` on capture maps view ids to the layout XML files that declare them. The TUI shows the decoded layout XML of the selected view (key `x`).

**Architecture:**
- `diff.py` builds a stable path for every node: `#id`, or the short class name plus a `[n]` suffix for repeated siblings. It compares the paths of two snapshots.
- `apk.py` decodes the APK with **aapt2** from the Android SDK build-tools, found the same way adb is found. It runs `dump resources` for id names and the list of layout files, and `dump xmltree` per layout for the elements. The result is a small `ApkIndex` (id → layout files, plus reconstructed XML) saved as `apk.json` in the snapshot. Nodes whose id is known get `props["apk"]["layouts"]`.
- Ruling vs spec: the spec named `androguard`. It is replaced by aapt2 because androguard pulls in ipython, SQLAlchemy and other heavy dependencies, and aapt2 is already on every machine that has the SDK.

**Tech Stack:** Python ≥3.10, textual, rich, typer, pytest, uv; aapt2 (SDK build-tools) at runtime, only when `--apk` is used.

**Spec:** `docs/superpowers/specs/2026-10-04-layoutcli-design.md` (Phase 3 row).

## Global Constraints
- Every module starts with `from __future__ import annotations`. File I/O uses `encoding="utf-8"`.
- APK decoding is opt-in (`--apk`). A failure there is recorded as `capabilities["apk"] = reason` and never fails the capture.
- The APK is never stored in the snapshot (22MB on the real device). Only `apk.json` is stored, holding the layouts that declare ids present in this snapshot.
- aapt2 output may have CRLF line endings (Windows); the parsers use `splitlines()`.
- No attribution trailers in commits. Run tests with `uv run pytest`.

## Review Focus
1. **Repeated siblings** (RecyclerView rows sharing an id) are matched by order: `#item_title[1]`, `#item_title[2]`. A diff of identical snapshots must be empty. The test is in Task 1.
2. **A merged snapshot compared with a uiautomator-only snapshot.** The root path is `/` regardless of root class. Properties present on only one side are not reported as changes. The test is in Task 1.
3. **aapt2 missing** (SDK without build-tools) with `--apk` → the capture succeeds and `capabilities["apk"]` explains why. The test is in Task 4.
4. **An unversioned `res/layout/x.xml` without attributes next to `res/layout-v1/x.xml` with them** (seen with aapt2). Ids must still be indexed, and the XML shown is the one with attributes. The test is in Task 3.
5. **`x` pressed on a view without an APK mapping** → notification only. The test is in Task 5.

---

### Task 1: Diff core

**Files:**
- Create: `src/layoutcli/diff.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Produces:
  - `node_paths(root) -> dict[str, ViewNode]`, in pre-order; the root is `/` and children are `/seg/seg`
  - `NodeChange(path, a, b, fields: dict[str, tuple[str, str]])`
  - `DiffResult(added: list[tuple[str, ViewNode]], removed: ..., changed: list[NodeChange])` with `.empty`
  - `diff_snapshots(a: ViewNode, b: ViewNode) -> DiffResult`; only the top-most added or removed node of a subtree is listed

- [ ] **Step 1: Write the failing test**

`tests/test_diff.py`:
```python
import copy

from helpers import find_by_id, views_snapshot

from layoutcli.diff import diff_snapshots, node_paths
from layoutcli.model import Rect, ViewNode


def test_paths_use_ids_classes_and_sibling_indexes():
    paths = node_paths(views_snapshot().root)
    assert "/" in paths
    assert "/LinearLayout/#content/#root/#toolbar" in paths
    assert "/LinearLayout/#content/#root/#list/#item_title[1]" in paths
    assert "/LinearLayout/#content/#root/#list/#item_title[2]" in paths


def test_identical_snapshots_have_no_diff():
    assert diff_snapshots(views_snapshot().root, views_snapshot().root).empty


def test_changed_fields_are_reported():
    a, b = views_snapshot().root, views_snapshot().root
    title = find_by_id(b, "toolbar").children[0]
    title.text = "Settings"
    find_by_id(b, "toolbar").bounds = Rect(0, 63, 1080, 250)
    result = diff_snapshots(a, b)
    by_path = {c.path: c.fields for c in result.changed}
    assert by_path["/LinearLayout/#content/#root/#toolbar"] == {
        "bounds": ("[0,63][1080,210]", "[0,63][1080,250]")}
    assert by_path["/LinearLayout/#content/#root/#toolbar/AppCompatTextView"] == {"text": ("Demo", "Settings")}
    assert not result.added and not result.removed


def test_only_topmost_added_and_removed_nodes_are_listed():
    a, b = views_snapshot().root, views_snapshot().root
    host = find_by_id(b, "compose_host")
    find_by_id(b, "root").children.remove(host)
    extra = ViewNode("a.Banner", id="banner", children=[ViewNode("a.Text", text="Hi")])
    find_by_id(b, "root").children.append(extra)
    result = diff_snapshots(a, b)
    assert [p for p, _ in result.removed] == ["/LinearLayout/#content/#root/#compose_host"]
    assert [p for p, _ in result.added] == ["/LinearLayout/#content/#root/#banner"]


def test_props_present_on_one_side_only_are_ignored_and_root_class_does_not_matter():
    a = ViewNode("DecorView", bounds=Rect(0, 0, 10, 10), props={"dumpsys": {"hash": "1"}},
                 children=[ViewNode("a.B", id="x", props={"uiautomator": {"clickable": "true"}})])
    b = ViewNode("android.widget.FrameLayout", bounds=Rect(0, 0, 10, 10),
                 children=[ViewNode("a.B", id="x", props={})])
    result = diff_snapshots(a, b)
    assert [c.fields for c in result.changed] == [{"class": ("DecorView", "android.widget.FrameLayout")}]


def test_compared_props_detect_state_changes():
    a, b = views_snapshot().root, views_snapshot().root
    find_by_id(b, "fab_small").props["uiautomator"]["enabled"] = "false"
    (change,) = diff_snapshots(a, b).changed
    assert change.fields == {"enabled": ("true", "false")}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diff.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.diff'`

- [ ] **Step 3: Implement `src/layoutcli/diff.py`**

```python
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from layoutcli.model import ViewNode

COMPARED_PROPS = ("content-desc", "clickable", "enabled", "checked", "selected", "focused")


@dataclass(eq=False)
class NodeChange:
    path: str
    a: ViewNode
    b: ViewNode
    fields: dict[str, tuple[str, str]]


@dataclass
class DiffResult:
    added: list[tuple[str, ViewNode]] = field(default_factory=list)
    removed: list[tuple[str, ViewNode]] = field(default_factory=list)
    changed: list[NodeChange] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def _key(node: ViewNode) -> str:
    return f"#{node.id}" if node.id else node.short_class


def node_paths(root: ViewNode) -> dict[str, ViewNode]:
    paths: dict[str, ViewNode] = {}

    def visit(node: ViewNode, path: str) -> None:
        paths[path] = node
        counts = Counter(_key(c) for c in node.children)
        seen: Counter[str] = Counter()
        for child in node.children:
            key = _key(child)
            seen[key] += 1
            segment = key if counts[key] == 1 else f"{key}[{seen[key]}]"
            visit(child, ("" if path == "/" else path) + "/" + segment)

    visit(root, "/")
    return paths


def _parent(path: str) -> str:
    parent = path.rsplit("/", 1)[0]
    return parent or "/"


def _fields(node: ViewNode) -> dict[str, str]:
    values = {"class": node.class_name, "bounds": str(node.bounds) if node.bounds else "",
              "visibility": node.visibility, "text": node.text or ""}
    ui = node.props.get("uiautomator", {})
    for key in COMPARED_PROPS:
        if key in ui:
            values[key] = ui[key]
    return values


def diff_snapshots(a: ViewNode, b: ViewNode) -> DiffResult:
    pa, pb = node_paths(a), node_paths(b)
    result = DiffResult()
    for path, node in pa.items():
        if path not in pb:
            if _parent(path) in pb or path == "/":
                result.removed.append((path, node))
            continue
        fa, fb = _fields(node), _fields(pb[path])
        changed = {k: (fa[k], fb[k]) for k in fa if k in fb and fa[k] != fb[k]}
        if changed:
            result.changed.append(NodeChange(path, node, pb[path], changed))
    for path, node in pb.items():
        if path not in pa and _parent(path) in pa:
            result.added.append((path, node))
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diff.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

`git add src/layoutcli/diff.py tests/test_diff.py && git commit -m "feat: snapshot diff core"`

---

### Task 2: `layoutcli diff` command

**Files:**
- Modify: `src/layoutcli/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `diff_snapshots` (Task 1); `_choose_snapshot` and `_resolve_snapshot` (existing)
- Produces:
  - `_choose_snapshot(title: str = "Snapshot")`, which takes a prompt label
  - `_resolve_snapshot(snapshot_dir, adb, serial, title="Snapshot")`
  - The command `diff [A] [B] [-s] [--adb]`. A missing argument is picked interactively (prompts "First snapshot" and "Second snapshot"; `n` captures now). It prints the `+`/`-`/`~` lines and a summary, and always exits 0.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:
```python
def test_diff_prints_changes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    save_capture(views_raw(), views_snapshot(), a)
    changed = views_snapshot()
    next(n for n, _ in changed.root.walk() if n.text == "Demo").text = "Settings"
    save_capture(views_raw(), changed, b)
    result = runner.invoke(cli.app, ["diff", str(a), str(b)])
    assert result.exit_code == 0, result.output
    assert '~ /LinearLayout/#content/#root/#toolbar/AppCompatTextView  text "Demo" -> "Settings"' in result.output
    assert "0 added, 0 removed, 1 changed" in result.output


def test_diff_identical_snapshots(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path / "a")
    result = runner.invoke(cli.app, ["diff", str(tmp_path / "a"), str(tmp_path / "a")])
    assert result.exit_code == 0
    assert "no differences" in result.output


def test_diff_picks_missing_snapshots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _saved(tmp_path, "capture-old", "2026-10-01T10:00:00+00:00", "com.a.Old")
    _saved(tmp_path, "capture-new", "2026-10-05T10:00:00+00:00", "com.a.New")
    result = runner.invoke(cli.app, ["diff"], input="2\n1\n")
    assert result.exit_code == 0, result.output
    assert "First snapshot" in result.output and "Second snapshot" in result.output
    assert "capture-old" in result.output.splitlines()[-2] or "no differences" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL with `No such command 'diff'`

- [ ] **Step 3: Implement in `src/layoutcli/cli.py`**

- Change `_choose_snapshot` to `def _choose_snapshot(title: str = "Snapshot") -> Path | None:`. Print `console.print(f"[bold]{title}[/]")` before the list, and use `typer.prompt(title, default="1")`.
- Change `_resolve_snapshot(snapshot_dir, adb, serial, title: str = "Snapshot")` to pass `title` through.
- Add `from layoutcli.diff import diff_snapshots`, then:
```python
def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


@app.command()
def diff(first: Annotated[Optional[Path], typer.Argument(help="Older snapshot (picked when omitted).")] = None,
         second: Annotated[Optional[Path], typer.Argument(help="Newer snapshot (picked when omitted).")] = None,
         serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """Show views added, removed and changed between two snapshots."""
    try:
        dir_a = _resolve_snapshot(first, adb, serial, "First snapshot")
        dir_b = _resolve_snapshot(second, adb, serial, "Second snapshot")
        snap_a, snap_b = load_snapshot(dir_a), load_snapshot(dir_b)
    except (AdbError, BuildError, SnapshotError) as e:
        raise _fail(e)
    console.print(f"{escape(dir_a.name)} ({escape(snap_a.activity or '?')}) -> "
                  f"{escape(dir_b.name)} ({escape(snap_b.activity or '?')})")
    result = diff_snapshots(snap_a.root, snap_b.root)
    if result.empty:
        console.print("no differences")
        return
    for path, node in result.removed:
        console.print(f"[red]- {escape(path)}[/]  {escape(node_label(node).plain)}")
    for path, node in result.added:
        console.print(f"[green]+ {escape(path)}[/]  {escape(node_label(node).plain)}")
    for change in result.changed:
        parts = "; ".join(f"{k} {_quote(old)} -> {_quote(new)}" for k, (old, new) in change.fields.items())
        console.print(f"[yellow]~ {escape(change.path)}[/]  {escape(parts)}")
    console.print(f"{len(result.added)} added, {len(result.removed)} removed, {len(result.changed)} changed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

`git commit -am "feat: layoutcli diff command"` (also add `tests/test_cli.py`)

---

### Task 3: APK index via aapt2

**Files:**
- Create: `src/layoutcli/apk.py`
- Fixtures (already generated from the real aapt2 2.20): `tests/fixtures/mini.apk`, `tests/fixtures/aapt2_resources.txt`, `tests/fixtures/aapt2_xmltree_activity_main.txt`. `mini.apk` declares ids `root`, `toolbar`, `fab_small` (in `activity_main.xml`) and `item_title` (in `item_row.xml`), plus `dimen/screen_padding`.
- Test: `tests/test_apk.py`

**Interfaces:**
- Produces:
  - `ApkError(Exception)`
  - `find_aapt2(adb_path: Path | None = None, env=None) -> Path`, which takes the highest build-tools version found under the adb SDK, then `ANDROID_HOME`, `ANDROID_SDK_ROOT`, then `%LOCALAPPDATA%\Android\Sdk`
  - `parse_resources_dump(text) -> tuple[dict[str, str], list[str]]`, giving hex id → `type/name` plus the layout files
  - `parse_xmltree(text, names) -> tuple[str, list[str]]`, giving the reconstructed XML plus the id names declared
  - `ApkIndex(ids: dict[str, list[str]], layouts: dict[str, str])` with `to_dict()`, `from_dict()` and `restricted_to(ids: set[str]) -> ApkIndex`
  - `build_index(apk_path, aapt2, runner=None) -> ApkIndex`, where the runner is `(args) -> str`, the default runs subprocess, and calls are made in parallel (8 threads)
  - `apply_index(root: ViewNode, index) -> int`, which sets `props["apk"] = {"layouts": "a.xml, b.xml"}` and returns the number of nodes mapped

- [ ] **Step 1: Write the failing test**

`tests/test_apk.py`:
```python
import shutil
from pathlib import Path

import pytest
from helpers import FIXTURES, find_by_id, read_fixture, views_snapshot

from layoutcli.apk import (ApkError, ApkIndex, apply_index, build_index, find_aapt2, parse_resources_dump,
                           parse_xmltree)

AAPT2 = "aapt2.exe" if __import__("os").name == "nt" else "aapt2"


def make_build_tools(sdk: Path, *versions: str) -> dict[str, Path]:
    made = {}
    for v in versions:
        d = sdk / "build-tools" / v
        d.mkdir(parents=True)
        (d / AAPT2).write_bytes(b"")
        made[v] = d / AAPT2
    return made


def test_find_aapt2_prefers_highest_version_next_to_adb(tmp_path):
    made = make_build_tools(tmp_path / "sdk", "34.0.0", "36.0.0", "9.0.0")
    adb = tmp_path / "sdk" / "platform-tools" / "adb.exe"
    assert find_aapt2(adb, env={}) == made["36.0.0"]


def test_find_aapt2_missing_raises(tmp_path):
    with pytest.raises(ApkError, match="aapt2 not found"):
        find_aapt2(tmp_path / "nosdk" / "platform-tools" / "adb.exe", env={})


def test_parse_resources_dump():
    names, layouts = parse_resources_dump(read_fixture("aapt2_resources.txt"))
    assert names["0x7f020003"] == "id/toolbar"
    assert names["0x7f010000"] == "dimen/screen_padding"
    assert layouts == ["res/layout/activity_main.xml", "res/layout-v1/activity_main.xml",
                       "res/layout/item_row.xml", "res/layout-v1/item_row.xml"]


def test_parse_xmltree_reconstructs_readable_xml():
    names, _ = parse_resources_dump(read_fixture("aapt2_resources.txt"))
    xml, ids = parse_xmltree(read_fixture("aapt2_xmltree_activity_main.txt"), names)
    assert ids == ["root", "toolbar", "fab_small"]
    assert xml.splitlines() == [
        '<LinearLayout android:orientation="1" android:id="@id/root" android:padding="@dimen/screen_padding" '
        'android:layout_width="match_parent" android:layout_height="match_parent">',
        '    <TextView android:id="@id/toolbar" android:layout_width="match_parent" '
        'android:layout_height="56dp" android:text="Demo"/>',
        '    <ImageButton android:id="@id/fab_small" android:layout_width="34dp" android:layout_height="34dp"/>',
        "</LinearLayout>",
    ]


def test_index_round_trip_restrict_and_apply():
    index = ApkIndex(ids={"toolbar": ["res/layout/activity_main.xml"], "other": ["res/layout/x.xml"]},
                     layouts={"res/layout/activity_main.xml": "<A/>", "res/layout/x.xml": "<X/>"})
    small = index.restricted_to({"toolbar", "fab_small"})
    assert small.to_dict() == {"ids": {"toolbar": ["res/layout/activity_main.xml"]},
                               "layouts": {"res/layout/activity_main.xml": "<A/>"}}
    assert ApkIndex.from_dict(small.to_dict()).to_dict() == small.to_dict()
    snap = views_snapshot()
    assert apply_index(snap.root, index) == 1
    assert find_by_id(snap.root, "toolbar").props["apk"] == {"layouts": "res/layout/activity_main.xml"}


def test_build_index_with_fake_runner_prefers_files_with_attributes():
    resources = read_fixture("aapt2_resources.txt")
    tree = read_fixture("aapt2_xmltree_activity_main.txt")
    bare = "N: android=http://schemas.android.com/apk/res/android (line=2)\n  E: LinearLayout (line=2)\n"

    def runner(args):
        if args[1] == "resources":
            return resources
        file = args[args.index("--file") + 1]
        if file.endswith("activity_main.xml"):
            return tree if "layout-v1" in file else bare
        return bare.replace("LinearLayout", "TextView")

    index = build_index(Path("x.apk"), Path("aapt2"), runner=runner)
    assert index.ids["toolbar"] == ["res/layout-v1/activity_main.xml"]
    assert "android:id=\"@id/toolbar\"" in index.layouts["res/layout-v1/activity_main.xml"]


@pytest.mark.skipif(not shutil.which("aapt2") and not list(
    (Path(__import__("os").environ.get("LOCALAPPDATA", "/nonexistent")) / "Android" / "Sdk" / "build-tools").glob("*/aapt2*")),
    reason="aapt2 not installed")
def test_build_index_with_real_aapt2():
    index = build_index(FIXTURES / "mini.apk", find_aapt2(None))
    assert set(index.ids) == {"root", "toolbar", "fab_small", "item_title"}
    assert index.ids["item_title"] == ["res/layout-v1/item_row.xml"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_apk.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.apk'`

- [ ] **Step 3: Implement `src/layoutcli/apk.py`**

```python
from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping
from xml.sax.saxutils import quoteattr

from layoutcli.model import ViewNode

AAPT2_NAME = "aapt2.exe" if os.name == "nt" else "aapt2"
_RESOURCE_RE = re.compile(r"^\s*resource (0x[0-9a-f]+) (\S+)")
_FILE_RE = re.compile(r"\(file\) (\S+) type=XML")
_ELEMENT_RE = re.compile(r"^(\s*)E: (\S+)")
_ATTR_RE = re.compile(r"^\s*A: (\S+?)\(0x[0-9a-f]+\)=(.*)$|^\s*A: ([^=(]+)=(.*)$")
_NAMESPACES = {"http://schemas.android.com/apk/res/android": "android",
               "http://schemas.android.com/apk/res-auto": "app",
               "http://schemas.android.com/tools": "tools"}
_LAYOUT_SIZES = {"-1": "match_parent", "-2": "wrap_content"}


class ApkError(Exception):
    pass


def _version_key(path: Path) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.-]", path.parent.name))


def find_aapt2(adb_path: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    sdks: list[Path] = []
    if adb_path is not None:
        sdks.append(Path(adb_path).parent.parent)
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if env.get(var):
            sdks.append(Path(env[var]))
    if env.get("LOCALAPPDATA"):
        sdks.append(Path(env["LOCALAPPDATA"]) / "Android" / "Sdk")
    for sdk in sdks:
        found = sorted((sdk / "build-tools").glob(f"*/{AAPT2_NAME}"), key=_version_key, reverse=True)
        if found:
            return found[0]
    raise ApkError("aapt2 not found (install Android SDK build-tools); tried: "
                   + ", ".join(str(s / "build-tools") for s in sdks))


def parse_resources_dump(text: str) -> tuple[dict[str, str], list[str]]:
    names: dict[str, str] = {}
    layouts: list[str] = []
    current = ""
    for line in text.splitlines():
        m = _RESOURCE_RE.match(line)
        if m:
            names[m.group(1)] = current = m.group(2)
            continue
        f = _FILE_RE.search(line)
        if f and current.startswith("layout/"):
            layouts.append(f.group(1))
    return names, layouts


def _attr_name(raw: str) -> str:
    if ":" in raw and raw.startswith("http"):
        uri, local = raw.rsplit(":", 1)
        return f"{_NAMESPACES.get(uri, uri.rsplit('/', 1)[-1])}:{local}"
    return raw


def _attr_value(name: str, raw: str, names: dict[str, str]) -> str:
    raw = raw.strip()
    if raw.startswith("@0x") or raw.startswith("?0x"):
        ref = names.get(raw[1:])
        return f"{raw[0]}{ref}" if ref else raw
    if raw.startswith('"'):
        m = re.match(r'"((?:[^"\\]|\\.)*)"', raw)
        return m.group(1) if m else raw
    if name.endswith(("layout_width", "layout_height")) and raw in _LAYOUT_SIZES:
        return _LAYOUT_SIZES[raw]
    m = re.fullmatch(r"(-?\d+)\.0+(dp|sp|px|dip)", raw)
    if m:
        return m.group(1) + m.group(2)
    return raw


@dataclass
class _Element:
    tag: str
    indent: int
    attrs: list[tuple[str, str]] = field(default_factory=list)
    children: list[_Element] = field(default_factory=list)


def parse_xmltree(text: str, names: dict[str, str]) -> tuple[str, list[str]]:
    roots: list[_Element] = []
    stack: list[_Element] = []
    ids: list[str] = []
    for line in text.splitlines():
        m = _ELEMENT_RE.match(line)
        if m:
            element = _Element(m.group(2), len(m.group(1)))
            while stack and stack[-1].indent >= element.indent:
                stack.pop()
            (stack[-1].children if stack else roots).append(element)
            stack.append(element)
            continue
        a = _ATTR_RE.match(line)
        if a and stack:
            raw_name = a.group(1) or a.group(3)
            raw_value = a.group(2) if a.group(1) else a.group(4)
            name = _attr_name(raw_name.strip())
            value = _attr_value(name, raw_value, names)
            stack[-1].attrs.append((name, value))
            if name == "android:id" and value.startswith("@id/"):
                ids.append(value[4:])
    lines: list[str] = []

    def emit(element: _Element, depth: int) -> None:
        attrs = "".join(f" {k}={quoteattr(v)}" for k, v in element.attrs)
        pad = "    " * depth
        if element.children:
            lines.append(f"{pad}<{element.tag}{attrs}>")
            for child in element.children:
                emit(child, depth + 1)
            lines.append(f"{pad}</{element.tag}>")
        else:
            lines.append(f"{pad}<{element.tag}{attrs}/>")

    for root in roots:
        emit(root, 0)
    return "\n".join(lines), ids


@dataclass
class ApkIndex:
    ids: dict[str, list[str]] = field(default_factory=dict)
    layouts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ids": {k: list(v) for k, v in self.ids.items()}, "layouts": dict(self.layouts)}

    @classmethod
    def from_dict(cls, d: dict) -> ApkIndex:
        return cls(ids={k: list(v) for k, v in d.get("ids", {}).items()}, layouts=dict(d.get("layouts", {})))

    def restricted_to(self, ids: set[str]) -> ApkIndex:
        kept = {k: v for k, v in self.ids.items() if k in ids}
        files = {f for v in kept.values() for f in v}
        return ApkIndex(kept, {f: x for f, x in self.layouts.items() if f in files})


def _run(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ApkError(f"cannot run aapt2: {e}") from e
    if proc.returncode != 0:
        raise ApkError(f"aapt2 failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def build_index(apk_path: Path, aapt2: Path, runner: Callable[[list[str]], str] | None = None) -> ApkIndex:
    run = runner or _run
    names, layout_files = parse_resources_dump(run([str(aapt2), "dump", "resources", str(apk_path)]))

    def decode(file: str) -> tuple[str, str, list[str]]:
        xml, ids = parse_xmltree(run([str(aapt2), "dump", "xmltree", "--file", file, str(apk_path)]), names)
        return file, xml, ids

    with ThreadPoolExecutor(max_workers=8) as pool:
        decoded = list(pool.map(decode, layout_files))
    index = ApkIndex()
    for file, xml, ids in decoded:
        if not ids:
            continue  # e.g. an unversioned copy without attributes
        index.layouts[file] = xml
        for view_id in ids:
            index.ids.setdefault(view_id, [])
            if file not in index.ids[view_id]:
                index.ids[view_id].append(file)
    return index


def apply_index(root: ViewNode, index: ApkIndex) -> int:
    mapped = 0
    for node, _ in root.walk():
        if node.id and node.id in index.ids:
            node.props["apk"] = {"layouts": ", ".join(index.ids[node.id])}
            mapped += 1
    return mapped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_apk.py -q`
Expected: 7 passed (the real-aapt2 test runs on this machine)

- [ ] **Step 5: Commit**

`git add src/layoutcli/apk.py tests/test_apk.py tests/fixtures/mini.apk tests/fixtures/aapt2_*.txt && git commit -m "feat: APK layout index via aapt2"`

---

### Task 4: `--apk PATH|device` on capture

**Files:**
- Modify: `src/layoutcli/capture.py` (add `pull_apk`), `src/layoutcli/snapshot_io.py` (`save_apk_index`, `load_apk_index`), `src/layoutcli/cli.py`
- Test: `tests/test_capture.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `pull_apk(adb, package: str, dest: Path) -> Path`, which reads `pm path` (first `package:` line, the base APK), `exec-out cat` the file into `dest`, and raises `AdbError` on failure
  - `save_apk_index(index, out_dir)` writes `apk.json`; `load_apk_index(dir) -> ApkIndex | None`
  - `capture`, `inspect` and the `main` callback accept `--apk TEXT` (a local path or `device`)
  - `capabilities["apk"]` is `"ok (N views mapped)"` or the reason it failed
  - Module-level `cli.find_aapt2` and `cli.build_index` (patched in tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capture.py`:
```python
import pytest

from layoutcli.capture import pull_apk


def test_pull_apk_writes_base_apk(tmp_path):
    responses = views_responses()
    responses["pm path com.example.demo"] = b"package:/data/app/~~x==/com.example.demo-y==/base.apk\npackage:/data/app/~~x==/split_config.arm64_v8a.apk\n"
    responses["cat /data/app/~~x==/com.example.demo-y==/base.apk"] = b"PK\x03\x04apk-bytes"
    dest = pull_apk(FakeAdb(responses), "com.example.demo", tmp_path / "base.apk")
    assert dest.read_bytes() == b"PK\x03\x04apk-bytes"


def test_pull_apk_unknown_package(tmp_path):
    with pytest.raises(AdbError, match="not installed"):
        pull_apk(FakeAdb(views_responses()), "com.missing", tmp_path / "base.apk")
```

Append to `tests/test_cli.py`:
```python
from layoutcli.apk import ApkError, ApkIndex
from layoutcli.snapshot_io import load_apk_index


def test_capture_with_local_apk_maps_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    monkeypatch.setattr(cli, "find_aapt2", lambda adb_path=None: tmp_path / "aapt2")
    monkeypatch.setattr(cli, "build_index", lambda apk, aapt2: ApkIndex(
        ids={"toolbar": ["res/layout/activity_main.xml"], "unused": ["res/layout/x.xml"]},
        layouts={"res/layout/activity_main.xml": "<A/>", "res/layout/x.xml": "<X/>"}))
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    out = tmp_path / "snap"
    result = runner.invoke(cli.app, ["capture", "-o", str(out), "--apk", str(apk)])
    assert result.exit_code == 0, result.output
    assert "ok (1 views mapped)" in result.output
    assert load_apk_index(out).to_dict() == {"ids": {"toolbar": ["res/layout/activity_main.xml"]},
                                             "layouts": {"res/layout/activity_main.xml": "<A/>"}}


def test_capture_apk_failure_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))

    def no_aapt2(adb_path=None):
        raise ApkError("aapt2 not found (install Android SDK build-tools)")
    monkeypatch.setattr(cli, "find_aapt2", no_aapt2)
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    result = runner.invoke(cli.app, ["capture", "-o", str(tmp_path / "snap"), "--apk", str(apk)])
    assert result.exit_code == 0, result.output
    assert "aapt2 not found" in result.output
    assert load_apk_index(tmp_path / "snap") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capture.py tests/test_cli.py -q`
Expected: FAIL (`cannot import name 'pull_apk'`; `No such option: --apk`)

- [ ] **Step 3: Implement**

Append to `src/layoutcli/capture.py`:
```python
def pull_apk(adb: DeviceShell, package: str, dest: Path) -> Path:
    paths = [line[len("package:"):].strip() for line in _text(adb.exec_out(f"pm path {package}")).splitlines()
             if line.startswith("package:")]
    if not paths:
        raise AdbError(f"package {package} is not installed on the device")
    base = next((p for p in paths if p.endswith("/base.apk")), paths[0])
    data = adb.exec_out(f"cat {base}", timeout=600.0)
    if not data.startswith(b"PK"):
        raise AdbError(f"could not read {base}")
    dest.write_bytes(data)
    return dest
```
(add `from pathlib import Path` to the imports)

Append to `src/layoutcli/snapshot_io.py`:
```python
APK_FILE = "apk.json"


def save_apk_index(index: "ApkIndex", out_dir: Path) -> None:
    (out_dir / APK_FILE).write_text(json.dumps(index.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")


def load_apk_index(directory: Path) -> "ApkIndex | None":
    from layoutcli.apk import ApkIndex
    file = directory / APK_FILE
    if not file.is_file():
        return None
    try:
        return ApkIndex.from_dict(json.loads(file.read_text(encoding="utf-8")))
    except (ValueError, TypeError):
        return None
```
(the annotations are strings, so no import cycle: `apk.py` imports only `model`)

In `src/layoutcli/cli.py`:
- Imports: `import tempfile`, `from layoutcli.apk import ApkError, apply_index, build_index, find_aapt2`, `from layoutcli.capture import capture_raw, pull_apk`, `from layoutcli.snapshot_io import ... save_apk_index`.
- `ApkOpt = Annotated[Optional[str], typer.Option("--apk", help="Map ids to layout XML: path to the app's APK, or 'device' to pull it.")]`
- `_capture(adb_path, serial, out, apk: str | None = None)`. After `snap = build_snapshot(...)` and before saving:
```python
    index = None
    if apk:
        try:
            index = _apk_index(adb, apk, snap.package)
            mapped = apply_index(snap.root, index)
            snap.capabilities["apk"] = f"ok ({mapped} views mapped)"
        except (ApkError, AdbError) as e:
            snap.capabilities["apk"] = str(e)
```
After `save_capture(...)`: `if index is not None: save_apk_index(index.restricted_to({n.id for n, _ in snap.root.walk() if n.id}), out_dir)`.
- The helper:
```python
def _apk_index(adb: Adb, apk: str, package: str | None):
    aapt2 = find_aapt2(getattr(adb, "adb_path", None))
    if apk != "device":
        return build_index(Path(apk), aapt2)
    if not package:
        raise ApkError("cannot pull the APK: package unknown")
    with tempfile.TemporaryDirectory() as tmp:
        return build_index(pull_apk(adb, package, Path(tmp) / "base.apk"), aapt2)
```
- Add `apk: ApkOpt = None` to `capture`, `inspect` and `main`, and thread it through `_capture` and `_resolve_snapshot(..., apk=apk)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

`git commit -am "feat: --apk maps view ids to layout files"` (also add the new test changes)

---

### Task 5: TUI layout XML view (`x`)

**Files:**
- Modify: `src/layoutcli/tui/app.py`
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `load_apk_index` (Task 4)
- Produces:
  - `LayoutXmlScreen(title, xml, view_id)`, a `ModalScreen` with a scrollable `Static`. The line declaring `android:id="@id/<view_id>"` is shown reversed and bold. Escape or `q` closes it.
  - The binding `x` ("Layout XML") on `LayoutApp`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tui.py`:
```python
from layoutcli.apk import ApkIndex
from layoutcli.snapshot_io import save_apk_index


def test_x_shows_layout_xml_for_selected_view(tmp_path):
    snap = views_snapshot()
    save_capture(views_raw(), snap, tmp_path)
    save_apk_index(ApkIndex(ids={"toolbar": ["res/layout/activity_main.xml"]},
                            layouts={"res/layout/activity_main.xml":
                                     '<LinearLayout>\n    <TextView android:id="@id/toolbar"/>\n</LinearLayout>'}),
                   tmp_path)

    async def scenario(app, pilot):
        await pilot.press("slash", *"toolbar", "enter", "x")
        await pilot.pause()
        screen = app.screen
        assert screen.__class__.__name__ == "LayoutXmlScreen"
        assert 'android:id="@id/toolbar"' in screen.body.plain
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "LayoutXmlScreen"
    run_app(snap, scenario, base_dir=tmp_path)


def test_x_without_mapping_only_notifies():
    async def scenario(app, pilot):
        await pilot.press("x")
        await pilot.pause()
        assert app.screen.__class__.__name__ != "LayoutXmlScreen"
    run_app(views_snapshot(), scenario)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tui.py -q`
Expected: FAIL (no `x` binding, so no screen is pushed)

- [ ] **Step 3: Implement in `src/layoutcli/tui/app.py`**

- Imports: `from textual.screen import ModalScreen`, `from textual.containers import VerticalScroll`, `from textual.widgets import Static`, `from layoutcli.snapshot_io import load_apk_index`.
- The screen:
```python
class LayoutXmlScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("q", "dismiss", "Close")]
    DEFAULT_CSS = """
    LayoutXmlScreen { align: center middle; }
    #xml { width: 90%; height: 85%; border: round $primary; background: $surface; }
    """

    def __init__(self, title: str, xml: str, view_id: str):
        super().__init__()
        self.body = Text()
        marker = f'android:id="@id/{view_id}"'
        for i, line in enumerate(xml.splitlines()):
            if i:
                self.body.append("\n")
            self.body.append(line, style="bold reverse" if marker in line else "")
        self.title_text = title

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="xml"):
            yield Static(self.body)

    def on_mount(self) -> None:
        self.query_one("#xml").border_title = self.title_text
```
- In `LayoutApp.__init__`: `self._apk = load_apk_index(base_dir) if base_dir is not None else None`.
- Add `Binding("x", "layout_xml", "Layout XML")` to `BINDINGS`.
- The action:
```python
    def action_layout_xml(self) -> None:
        node = self.selected
        files = self._apk.ids.get(node.id, []) if (self._apk and node is not None and node.id) else []
        file = next((f for f in files if f in self._apk.layouts), None) if files else None
        if file is None:
            self.notify("no layout XML for this view (capture with --apk PATH or --apk device)")
            return
        self.push_screen(LayoutXmlScreen(f"{file}  (#{node.id})", self._apk.layouts[file], node.id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

`git commit -am "feat: TUI layout XML view for views mapped from the APK"`

---

### Task 6: README and a real-device check

- [ ] Add to the README usage list:
  - `layoutcli diff [A] [B]`
  - `--apk PATH|device` on `capture`, `inspect` and plain `layoutcli`, noting that it needs SDK build-tools (aapt2)
  - the `x` key
- [ ] Run against the device:
  1. `uv run layoutcli capture -o layout-snapshots/p3 --apk device`. Expect `apk ok (N views mapped)` (the Compose app has few views with ids; mapping 0 views is also correct).
  2. `uv run layoutcli diff layout-snapshots/p2 layout-snapshots/p3` after changing the screen on the phone.
  3. Run the TUI headless on `p3`.
- [ ] Commit: `git commit -am "docs: Phase 3 usage"`
