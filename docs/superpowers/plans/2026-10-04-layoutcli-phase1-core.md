# LayoutCli Phase 1 (Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `layoutcli capture` pulls a one-shot snapshot of the foreground Android screen (dumpsys View tree + uiautomator semantics + screenshot) into a folder, and `layoutcli inspect` opens it in a textual TUI with a hierarchy tree, a properties table and a box-drawing wireframe that highlights the selected view.

**Architecture:** `adb.py` locates and drives adb. `capture.py` runs the device commands and collects raw text and bytes into `RawCapture`, recording failures without raising. Pure parsers in `parse/` turn the raw text into trees. `merge.py` fuses the dumpsys tree (the backbone) with uiautomator nodes. `build.py` produces a `Snapshot`, and `snapshot_io.py` persists it. The TUI and the wireframe only read a `Snapshot`, so they also work offline. This deviates from the spec's `capture/<source>.py` layout on purpose: device I/O lives in one module and parsing is pure and separately testable.

**Tech Stack:** Python ≥3.10, textual, rich, typer, pytest, uv, hatchling.

**Spec:** `docs/superpowers/specs/2026-10-04-layoutcli-design.md`

**Out of scope here (later plans):** Phase 2 adds the screenshot preview, search/filter and checks. Phase 3 adds diff and APK decoding. Phase 4 adds JDWP deep properties.

## Global Constraints
- Python `>=3.10`. Every module starts with `from __future__ import annotations`.
- adb lookup order: `--adb` flag, then `ANDROID_HOME`, then `ANDROID_SDK_ROOT`, then `sdk.dir` in `local.properties` (searched from the cwd upward), then PATH, then the default SDK dirs (`%LOCALAPPDATA%\Android\Sdk`, `~/Library/Android/sdk`, `~/Android/Sdk`). The last entry is added beyond the spec because adb is not on PATH on the dev machine.
- No realtime mode: one capture per command.
- Graceful degradation: a failing source is recorded in `Snapshot.capabilities[source]` with its reason. Capture fails only when neither dumpsys nor uiautomator produced a tree.
- All file I/O passes `encoding="utf-8"` explicitly, because the Windows default is cp1252.
- Device commands go through `adb -s SERIAL exec-out CMD`. Output is decoded as UTF-8 with `errors="replace"` and `\r\n` is normalised to `\n`.
- Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run tests with `uv run pytest`.

## Review Focus
1. **Several, zero or unauthorized devices, and the `* daemon started` banner lines from `adb devices`.** Expect a clear error that names the serials and suggests `-s`, with the banner lines ignored. Tests are in Task 3.
2. **`uiautomator dump` failing with "could not get idle state" (the app is animating).** The capture should continue, the tree should come from dumpsys alone, and capabilities should show the reason. Tests are in Tasks 6 and 8.
3. **`local.properties` with a Java-escaped Windows path containing spaces (`sdk.dir=C\:\\Users\\...`).** The path should resolve correctly. The test is in Task 2.
4. **Non-ASCII view text (Cyrillic, emoji) and XML entities.** Text should parse correctly and survive save and load on Windows. Tests are in Tasks 4 and 8.
5. **Views with negative, off-screen or zero-size bounds.** They should parse, and the wireframe should clip them without crashing. Tests are in Tasks 5 and 9.

---

## File Structure

```
pyproject.toml, .gitignore, .gitattributes, README.md
src/layoutcli/
  __init__.py
  model.py          Rect, ViewNode, Snapshot, short_id (+ JSON dict conversion)
  adb.py            AdbError, ADB_NAME, find_adb, Adb (connect/exec_out), list_devices, select_device
  capture.py        RawCapture, DUMP_PATH, capture_raw(adb)
  parse/__init__.py
  parse/uiautomator.py  parse_uiautomator, parse_bounds
  parse/dumpsys.py      DNode, DumpsysResult, parse_dumpsys
  parse/wm.py           parse_wm_size, parse_wm_density
  merge.py          merge, pick_window, match_children, MergeError
  build.py          build_snapshot, BuildError
  snapshot_io.py    save_capture, load_snapshot, SnapshotError
  format.py         dp, size_text, node_label, node_rows
  wireframe.py      render_wireframe, visible_nodes
  tui/__init__.py
  tui/app.py        LayoutApp, Wireframe widget
  cli.py            typer app: capture, inspect
tests/
  helpers.py        read_fixture, find_by_id, FakeAdb, PNG_BYTES, views_responses, views_raw, views_snapshot
  fixtures/views_dumpsys.txt, fixtures/views_uiautomator.xml
  test_model.py test_adb_find.py test_adb_device.py test_parse_uiautomator.py test_parse_dumpsys.py
  test_capture.py test_merge.py test_build_io.py test_wireframe.py test_format.py test_tui.py test_cli.py
```

---

### Task 1: Project scaffold and data model

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.gitattributes`, `src/layoutcli/__init__.py`, `src/layoutcli/model.py`, `tests/helpers.py`
- Test: `tests/test_model.py`

**Interfaces:**
- Produces:
  - `Rect(left, top, right, bottom)` (frozen) with `.width`, `.height`, `.size -> tuple[int, int]`, `.offset(dx, dy) -> Rect`, `.to_list()`, `Rect.from_list(list)` and `str(r) == "[l,t][r,b]"`
  - `short_id(res: str | None) -> str | None`
  - `ViewNode(class_name, id=None, bounds=None, visibility="visible", text=None, sources=[], props={}, children=[])` with identity equality, plus `.short_class`, `.walk() -> Iterator[tuple[ViewNode, int]]`, `.to_dict()` and `ViewNode.from_dict()`
  - `Snapshot(root, screen, density, package=None, activity=None, device={}, captured_at="", capabilities={}, screenshot=None)` with `.to_dict()` (includes `"format": 1`) and `Snapshot.from_dict()`, which raises `ValueError` on an unknown format
  - `FORMAT_VERSION = 1`
  - In `tests/helpers.py`: `FIXTURES: Path`, `read_fixture(name) -> str`, `find_by_id(root, id) -> ViewNode`

- [ ] **Step 1: Scaffold the project**

`pyproject.toml`:
```toml
[project]
name = "layoutcli"
version = "0.1.0"
description = "Inspect Android app layouts from the terminal"
requires-python = ">=3.10"
dependencies = ["textual>=1.0", "rich>=13", "typer>=0.12"]

[project.scripts]
layoutcli = "layoutcli.cli:app"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/layoutcli"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
layout-snapshots/
```

`.gitattributes`:
```
* text=auto eol=lf
*.png binary
```

`src/layoutcli/__init__.py`:
```python
"""Android layout inspector CLI."""
```

Run: `uv sync`
Expected: `.venv` is created and textual, rich, typer and pytest are installed.

- [ ] **Step 2: Write the failing test**

`tests/test_model.py`:
```python
import json

import pytest

from layoutcli.model import Rect, Snapshot, ViewNode, short_id


def test_rect_size_offset_and_str():
    r = Rect(10, 20, 110, 70)
    assert r.size == (100, 50)
    assert r.offset(5, -5) == Rect(15, 15, 115, 65)
    assert str(r) == "[10,20][110,70]"
    assert Rect.from_list(r.to_list()) == r


def test_short_id():
    assert short_id("com.example:id/title") == "title"
    assert short_id("app:id/list") == "list"
    assert short_id("") is None
    assert short_id(None) is None


def test_walk_yields_nodes_with_depth():
    leaf = ViewNode("android.widget.TextView")
    root = ViewNode("android.widget.FrameLayout", children=[ViewNode("a.B", children=[leaf])])
    assert [(n.short_class, d) for n, d in root.walk()] == [
        ("FrameLayout", 0), ("B", 1), ("TextView", 2)]


def test_snapshot_round_trips_through_json():
    root = ViewNode(
        "DecorView", bounds=Rect(0, 0, 1080, 2400), sources=["dumpsys"],
        props={"dumpsys": {"hash": "abc"}},
        children=[ViewNode("android.widget.TextView", id="title", text="Привет 👋", visibility="gone")])
    snap = Snapshot(root=root, screen=(1080, 2400), density=420, package="com.example",
                    activity="com.example.Main", device={"serial": "emu"},
                    captured_at="2026-10-04T10:00:00+00:00", capabilities={"dumpsys": "ok"},
                    screenshot="screen.png")
    data = snap.to_dict()
    assert data["format"] == 1
    back = Snapshot.from_dict(json.loads(json.dumps(data)))
    assert back.to_dict() == data
    assert back.screen == (1080, 2400)
    assert back.root.children[0].text == "Привет 👋"


def test_snapshot_from_dict_rejects_unknown_format():
    with pytest.raises(ValueError, match="format"):
        Snapshot.from_dict({"format": 99})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.model'`

- [ ] **Step 4: Implement `src/layoutcli/model.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

FORMAT_VERSION = 1


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def offset(self, dx: int, dy: int) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def to_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]

    @classmethod
    def from_list(cls, values: list[int]) -> Rect:
        return cls(*values)

    def __str__(self) -> str:
        return f"[{self.left},{self.top}][{self.right},{self.bottom}]"


def short_id(res: str | None) -> str | None:
    """'com.example:id/title' or 'app:id/title' -> 'title'."""
    if not res:
        return None
    return res.rsplit("/", 1)[-1]


@dataclass(eq=False)
class ViewNode:
    class_name: str
    id: str | None = None
    bounds: Rect | None = None  # absolute screen pixels
    visibility: str = "visible"  # visible | invisible | gone
    text: str | None = None
    sources: list[str] = field(default_factory=list)
    props: dict[str, dict[str, str]] = field(default_factory=dict)  # source -> name -> value
    children: list[ViewNode] = field(default_factory=list)

    @property
    def short_class(self) -> str:
        return self.class_name.rsplit(".", 1)[-1]

    def walk(self, depth: int = 0) -> Iterator[tuple[ViewNode, int]]:
        yield self, depth
        for child in self.children:
            yield from child.walk(depth + 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "id": self.id,
            "bounds": self.bounds.to_list() if self.bounds else None,
            "visibility": self.visibility,
            "text": self.text,
            "sources": list(self.sources),
            "props": {k: dict(v) for k, v in self.props.items()},
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ViewNode:
        return cls(
            class_name=d["class"],
            id=d.get("id"),
            bounds=Rect.from_list(d["bounds"]) if d.get("bounds") else None,
            visibility=d.get("visibility", "visible"),
            text=d.get("text"),
            sources=list(d.get("sources", [])),
            props={k: dict(v) for k, v in d.get("props", {}).items()},
            children=[cls.from_dict(c) for c in d.get("children", [])],
        )


@dataclass(eq=False)
class Snapshot:
    root: ViewNode
    screen: tuple[int, int]
    density: int | None
    package: str | None = None
    activity: str | None = None
    device: dict[str, str] = field(default_factory=dict)
    captured_at: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)
    screenshot: str | None = None  # file name inside the snapshot dir

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_VERSION,
            "package": self.package,
            "activity": self.activity,
            "device": dict(self.device),
            "captured_at": self.captured_at,
            "screen": list(self.screen),
            "density": self.density,
            "capabilities": dict(self.capabilities),
            "screenshot": self.screenshot,
            "root": self.root.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        if d.get("format") != FORMAT_VERSION:
            raise ValueError(f"unsupported snapshot format {d.get('format')!r}")
        return cls(
            root=ViewNode.from_dict(d["root"]),
            screen=(int(d["screen"][0]), int(d["screen"][1])),
            density=d.get("density"),
            package=d.get("package"),
            activity=d.get("activity"),
            device=dict(d.get("device", {})),
            captured_at=d.get("captured_at", ""),
            capabilities=dict(d.get("capabilities", {})),
            screenshot=d.get("screenshot"),
        )
```

`tests/helpers.py`:
```python
from __future__ import annotations

from pathlib import Path

from layoutcli.model import ViewNode

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def find_by_id(root: ViewNode, view_id: str) -> ViewNode:
    for node, _ in root.walk():
        if node.id == view_id:
            return node
    raise AssertionError(f"no node with id {view_id!r}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_model.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .gitattributes src tests
git commit -m "feat: project scaffold and snapshot data model" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 2: adb discovery

**Files:**
- Create: `src/layoutcli/adb.py`
- Test: `tests/test_adb_find.py`

**Interfaces:**
- Consumes: none
- Produces: `AdbError(Exception)`, `ADB_NAME: str`, `find_adb(explicit: str | None = None, env: Mapping[str, str] | None = None, cwd: Path | None = None, which: Callable[[str], str | None] = shutil.which) -> Path`, `sdk_dir_from_local_properties(start: Path) -> Path | None`

- [ ] **Step 1: Write the failing test**

`tests/test_adb_find.py`:
```python
from pathlib import Path

import pytest

from layoutcli.adb import ADB_NAME, AdbError, find_adb


def make_sdk(root: Path) -> Path:
    tools = root / "platform-tools"
    tools.mkdir(parents=True)
    adb = tools / ADB_NAME
    adb.write_bytes(b"")
    return adb


def no_which(_name):
    return None


def test_explicit_file_wins(tmp_path):
    explicit = make_sdk(tmp_path / "explicit")
    make_sdk(tmp_path / "home")
    env = {"ANDROID_HOME": str(tmp_path / "home")}
    assert find_adb(str(explicit), env=env, cwd=tmp_path, which=no_which) == explicit


def test_explicit_directory_is_searched(tmp_path):
    adb = make_sdk(tmp_path / "sdk")
    assert find_adb(str(adb.parent), env={}, cwd=tmp_path, which=no_which) == adb


def test_explicit_missing_is_an_error(tmp_path):
    with pytest.raises(AdbError, match="adb not found at"):
        find_adb(str(tmp_path / "nope"), env={}, cwd=tmp_path, which=no_which)


def test_android_home_beats_local_properties(tmp_path):
    home_adb = make_sdk(tmp_path / "home")
    make_sdk(tmp_path / "props")
    (tmp_path / "local.properties").write_text(
        f"sdk.dir={(tmp_path / 'props').as_posix()}\n", encoding="utf-8")
    env = {"ANDROID_HOME": str(tmp_path / "home")}
    assert find_adb(env=env, cwd=tmp_path, which=no_which) == home_adb


def test_android_sdk_root_used_when_home_unset(tmp_path):
    adb = make_sdk(tmp_path / "root")
    assert find_adb(env={"ANDROID_SDK_ROOT": str(tmp_path / "root")}, cwd=tmp_path, which=no_which) == adb


def test_local_properties_with_escaped_windows_path_in_parent_dir(tmp_path):
    adb = make_sdk(tmp_path / "My Sdk")
    project = tmp_path / "proj"
    module = project / "app" / "src"
    module.mkdir(parents=True)
    escaped = str(tmp_path / "My Sdk").replace("\\", "\\\\").replace(":", "\\:")
    (project / "local.properties").write_text(
        f"## This file is automatically generated\nsdk.dir={escaped}\n", encoding="utf-8")
    assert find_adb(env={}, cwd=module, which=lambda _n: "/usr/bin/adb") == adb


def test_path_used_when_no_sdk_configured(tmp_path):
    assert find_adb(env={}, cwd=tmp_path, which=lambda n: f"/usr/bin/{n}") == Path("/usr/bin/adb")


def test_default_localappdata_sdk_is_last_resort(tmp_path):
    adb = make_sdk(tmp_path / "Android" / "Sdk")
    assert find_adb(env={"LOCALAPPDATA": str(tmp_path)}, cwd=tmp_path, which=no_which) == adb


def test_not_found_lists_what_was_tried(tmp_path):
    with pytest.raises(AdbError) as exc:
        find_adb(env={"ANDROID_HOME": str(tmp_path / "missing")}, cwd=tmp_path, which=no_which)
    message = str(exc.value)
    assert "adb not found" in message
    assert "missing" in message
    assert "--adb" in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_adb_find.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.adb'`

- [ ] **Step 3: Implement the discovery part of `src/layoutcli/adb.py`**

```python
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable, Mapping

ADB_NAME = "adb.exe" if os.name == "nt" else "adb"


class AdbError(Exception):
    """Any failure to locate adb or talk to a device."""


_PROP_RE = re.compile(r"^\s*([^=:\s]+)\s*[=:]\s*(.*)$")


def _parse_properties(text: str) -> dict[str, str]:
    """Minimal Java .properties reader: comments, key=value, backslash escapes."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "#!":
            continue
        m = _PROP_RE.match(line)
        if m:
            result[m.group(1)] = re.sub(r"\\(.)", r"\1", m.group(2).strip())
    return result


def sdk_dir_from_local_properties(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        props_file = directory / "local.properties"
        if props_file.is_file():
            sdk = _parse_properties(props_file.read_text(encoding="utf-8")).get("sdk.dir")
            return Path(sdk) if sdk else None
    return None


def _default_sdk_dirs(env: Mapping[str, str]) -> list[Path]:
    dirs: list[Path] = []
    if env.get("LOCALAPPDATA"):
        dirs.append(Path(env["LOCALAPPDATA"]) / "Android" / "Sdk")
    home = env.get("HOME") or env.get("USERPROFILE")
    if home:
        dirs += [Path(home) / "Library" / "Android" / "sdk", Path(home) / "Android" / "Sdk"]
    return dirs


def find_adb(
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd

    if explicit:
        path = Path(explicit)
        if path.is_dir():
            path = path / ADB_NAME
        if path.is_file():
            return path
        raise AdbError(f"adb not found at {explicit}")

    tried: list[str] = []

    def in_sdk(sdk: Path) -> Path | None:
        candidate = sdk / "platform-tools" / ADB_NAME
        tried.append(str(candidate))
        return candidate if candidate.is_file() else None

    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if env.get(var) and (found := in_sdk(Path(env[var]))):
            return found
    sdk = sdk_dir_from_local_properties(cwd)
    if sdk and (found := in_sdk(sdk)):
        return found
    on_path = which("adb")
    if on_path:
        return Path(on_path)
    tried.append("PATH")
    for default in _default_sdk_dirs(env):
        if found := in_sdk(default):
            return found
    raise AdbError(
        "adb not found (tried: " + ", ".join(tried) + "). "
        "Use --adb PATH, set ANDROID_HOME, or run inside a project with local.properties.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_adb_find.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/adb.py tests/test_adb_find.py
git commit -m "feat: locate adb via flag, env, local.properties, PATH, default SDK" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 3: Device selection and command runner

**Files:**
- Modify: `src/layoutcli/adb.py` (append)
- Test: `tests/test_adb_device.py`

**Interfaces:**
- Consumes: `AdbError` (Task 2)
- Produces:
  - `Runner = Callable[[list[str], float], tuple[int, bytes, bytes]]`
  - `list_devices(adb_path: Path, runner: Runner) -> list[tuple[str, str]]`, returning `(serial, state)` pairs
  - `select_device(devices, serial: str | None) -> str`
  - `Adb(adb_path: Path, serial: str, runner: Runner)` with attributes `.serial` and `.adb_path`
  - `Adb.connect(adb_path, serial=None, runner=_subprocess_runner) -> Adb`
  - `Adb.exec_out(cmd: str, timeout: float = 30.0) -> bytes`, which raises `AdbError` on a non-zero exit

- [ ] **Step 1: Write the failing test**

`tests/test_adb_device.py`:
```python
from pathlib import Path

import pytest

from layoutcli.adb import Adb, AdbError, list_devices, select_device

ADB = Path("adb")


def fake_runner(outputs):
    calls = []

    def run(args, timeout):
        calls.append(args)
        return outputs[tuple(args[1:])]

    run.calls = calls
    return run


def test_list_devices_ignores_daemon_banner():
    out = (b"* daemon not running; starting now at tcp:5037\n* daemon started successfully\n"
           b"List of devices attached\nemulator-5554\tdevice\nR58M123\tunauthorized\n\n")
    runner = fake_runner({("devices",): (0, out, b"")})
    assert list_devices(ADB, runner) == [("emulator-5554", "device"), ("R58M123", "unauthorized")]


def test_single_device_is_selected_automatically():
    assert select_device([("emulator-5554", "device")], None) == "emulator-5554"


def test_multiple_devices_require_serial():
    with pytest.raises(AdbError) as exc:
        select_device([("a1", "device"), ("b2", "device")], None)
    assert "a1" in str(exc.value) and "b2" in str(exc.value) and "-s" in str(exc.value)


def test_no_device_mentions_unauthorized_one():
    with pytest.raises(AdbError, match="R58M123 is unauthorized"):
        select_device([("R58M123", "unauthorized")], None)


def test_no_device_at_all():
    with pytest.raises(AdbError, match="no Android device connected"):
        select_device([], None)


def test_explicit_serial_must_exist_and_be_ready():
    devices = [("a1", "device"), ("b2", "offline")]
    assert select_device(devices, "a1") == "a1"
    with pytest.raises(AdbError, match="'zz' not found"):
        select_device(devices, "zz")
    with pytest.raises(AdbError, match="'b2' is offline"):
        select_device(devices, "b2")


def test_connect_and_exec_out_pass_serial():
    runner = fake_runner({
        ("devices",): (0, b"List of devices attached\nemu\tdevice\n", b""),
        ("-s", "emu", "exec-out", "wm size"): (0, b"Physical size: 1080x2400\n", b""),
    })
    adb = Adb.connect(ADB, None, runner)
    assert adb.serial == "emu"
    assert adb.exec_out("wm size") == b"Physical size: 1080x2400\n"


def test_exec_out_nonzero_exit_raises_with_stderr():
    runner = fake_runner({("-s", "emu", "exec-out", "bad"): (1, b"", b"/system/bin/sh: bad: not found\n")})
    with pytest.raises(AdbError, match="bad: not found"):
        Adb(ADB, "emu", runner).exec_out("bad")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_adb_device.py -v`
Expected: FAIL with `ImportError: cannot import name 'Adb'`

- [ ] **Step 3: Append to `src/layoutcli/adb.py`**

Add `import subprocess` to the imports, then append:

```python
Runner = Callable[[list[str], float], tuple[int, bytes, bytes]]


def _subprocess_runner(args: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise AdbError(f"cannot run adb: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb timed out after {timeout:g}s: {' '.join(args[1:])}") from e
    return proc.returncode, proc.stdout, proc.stderr


def list_devices(adb_path: Path, runner: Runner) -> list[tuple[str, str]]:
    code, out, err = runner([str(adb_path), "devices"], 15.0)
    if code != 0:
        raise AdbError(f"`adb devices` failed: {err.decode('utf-8', 'replace').strip()}")
    devices = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def select_device(devices: list[tuple[str, str]], serial: str | None) -> str:
    if serial is not None:
        for s, state in devices:
            if s == serial:
                if state != "device":
                    raise AdbError(f"device '{serial}' is {state}")
                return s
        known = ", ".join(s for s, _ in devices) or "none"
        raise AdbError(f"device '{serial}' not found; connected: {known}")
    ready = [s for s, state in devices if state == "device"]
    if len(ready) == 1:
        return ready[0]
    if len(ready) > 1:
        raise AdbError(f"multiple devices connected ({', '.join(ready)}): choose one with -s SERIAL")
    not_ready = [f"{s} is {state}" for s, state in devices]
    if not_ready:
        hint = " (accept the USB debugging prompt on the device)" if any(
            st == "unauthorized" for _, st in devices) else ""
        raise AdbError("no usable Android device: " + ", ".join(not_ready) + hint)
    raise AdbError("no Android device connected (check `adb devices`)")


class Adb:
    def __init__(self, adb_path: Path, serial: str, runner: Runner = _subprocess_runner):
        self.adb_path = adb_path
        self.serial = serial
        self._runner = runner

    @classmethod
    def connect(cls, adb_path: Path, serial: str | None = None,
                runner: Runner = _subprocess_runner) -> Adb:
        return cls(adb_path, select_device(list_devices(adb_path, runner), serial), runner)

    def exec_out(self, cmd: str, timeout: float = 30.0) -> bytes:
        code, out, err = self._runner(
            [str(self.adb_path), "-s", self.serial, "exec-out", cmd], timeout)
        if code != 0:
            detail = (err or out).decode("utf-8", "replace").strip()
            raise AdbError(f"`{cmd}` failed: {detail}")
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_adb_device.py tests/test_adb_find.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/adb.py tests/test_adb_device.py
git commit -m "feat: adb device selection and exec-out runner" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 4: uiautomator XML parser and fixture

**Files:**
- Create: `src/layoutcli/parse/__init__.py` (empty), `src/layoutcli/parse/uiautomator.py`, `tests/fixtures/views_uiautomator.xml`
- Test: `tests/test_parse_uiautomator.py`

**Interfaces:**
- Consumes: `Rect`, `ViewNode`, `short_id` (Task 1)
- Produces: `parse_uiautomator(xml: str) -> list[ViewNode]` (one per top-level window node; raises `xml.etree.ElementTree.ParseError` on bad XML) and `parse_bounds(s: str) -> Rect | None`. Each node has `sources == ["uiautomator"]` and `props["uiautomator"]` set to all of its XML attributes.

- [ ] **Step 1: Create the fixture**

This is a hand-written sample in uiautomator's format: a Views screen with a Compose host. The absolute bounds must line up with the dumpsys fixture in Task 5 (status bar 63px, toolbar 147px).

`tests/fixtures/views_uiautomator.xml`:
```xml
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2400]">
    <node index="0" text="" resource-id="" class="android.widget.LinearLayout" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2400]">
      <node index="1" text="" resource-id="android:id/content" class="android.widget.FrameLayout" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[0,63][1080,2400]">
        <node index="0" text="" resource-id="com.example.demo:id/root" class="android.view.ViewGroup" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[0,63][1080,2400]">
          <node index="0" text="" resource-id="com.example.demo:id/toolbar" class="android.view.ViewGroup" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[0,63][1080,210]">
            <node index="0" text="Demo" resource-id="" class="android.widget.TextView" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[42,101][300,172]" />
          </node>
          <node index="1" text="" resource-id="com.example.demo:id/list" class="androidx.recyclerview.widget.RecyclerView" package="com.example.demo" content-desc="" clickable="false" enabled="true" scrollable="true" bounds="[0,210][1080,1563]">
            <node index="0" text="First item" resource-id="com.example.demo:id/item_title" class="android.widget.TextView" package="com.example.demo" content-desc="" clickable="true" enabled="true" bounds="[0,210][1080,336]" />
            <node index="1" text="Second item" resource-id="com.example.demo:id/item_title" class="android.widget.TextView" package="com.example.demo" content-desc="" clickable="true" enabled="true" bounds="[0,336][1080,462]" />
          </node>
          <node index="2" text="" resource-id="com.example.demo:id/fab_small" class="android.widget.ImageButton" package="com.example.demo" content-desc="" clickable="true" enabled="true" bounds="[960,1583][1050,1673]" />
          <node index="3" text="" resource-id="com.example.demo:id/compose_host" class="android.view.ViewGroup" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[0,1863][1080,2400]">
            <node index="0" text="" resource-id="" class="android.view.View" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[0,1863][1080,2400]">
              <node index="0" text="Hello Compose" resource-id="" class="android.widget.TextView" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[42,1900][500,1960]" />
              <node index="1" text="" resource-id="" class="android.widget.Button" package="com.example.demo" content-desc="" clickable="true" enabled="true" bounds="[42,2000][400,2126]">
                <node index="0" text="Click me" resource-id="" class="android.widget.TextView" package="com.example.demo" content-desc="" clickable="false" enabled="true" bounds="[100,2040][340,2090]" />
              </node>
            </node>
          </node>
        </node>
      </node>
    </node>
  </node>
</hierarchy>
```

- [ ] **Step 2: Write the failing test**

`tests/test_parse_uiautomator.py`:
```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_parse_uiautomator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.parse'`

- [ ] **Step 4: Implement `src/layoutcli/parse/uiautomator.py`**

Also create an empty `src/layoutcli/parse/__init__.py`.

```python
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from layoutcli.model import Rect, ViewNode, short_id

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_bounds(s: str) -> Rect | None:
    m = _BOUNDS_RE.fullmatch(s.strip())
    return Rect(*(int(g) for g in m.groups())) if m else None


def parse_uiautomator(xml: str) -> list[ViewNode]:
    """Parse `uiautomator dump` output into one ViewNode tree per top-level window node."""
    root = ET.fromstring(xml.strip().encode("utf-8"))
    return [_convert(el) for el in root.findall("node")]


def _convert(el: ET.Element) -> ViewNode:
    attrs = dict(el.attrib)
    node = ViewNode(
        class_name=attrs.get("class") or "?",
        id=short_id(attrs.get("resource-id")),
        bounds=parse_bounds(attrs.get("bounds", "")),
        text=attrs.get("text") or None,
        sources=["uiautomator"],
        props={"uiautomator": attrs},
    )
    node.children = [_convert(child) for child in el.findall("node")]
    return node
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_parse_uiautomator.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/layoutcli/parse tests/test_parse_uiautomator.py tests/fixtures/views_uiautomator.xml
git commit -m "feat: parse uiautomator XML dumps" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 5: dumpsys View hierarchy parser and fixture

**Files:**
- Create: `src/layoutcli/parse/dumpsys.py`, `tests/fixtures/views_dumpsys.txt`
- Test: `tests/test_parse_dumpsys.py`

**Interfaces:**
- Consumes: `Rect` (Task 1)
- Produces:
  - `DNode(class_name, hash, flags="", rel=None, res_id=None, children=[])` with `.visibility` (`"visible" | "invisible" | "gone"`) and `.clickable: bool`. `rel` is the parent-relative `Rect`, and it is `None` for the `DecorView@...` root line.
  - `DumpsysResult(package, activity, root: DNode)`
  - `parse_dumpsys(text: str) -> DumpsysResult | None`, which prefers the activity block containing `mResumed=true` and otherwise takes the last block that has a hierarchy

`View.toString()` format reference: `cls{hash VFEDHVCLX PFSHAIDx left,top-right,bottom #hexid pkg:id/name aid=N}`. In the first flag group, character 0 is the visibility (`V`, `I` or `G`) and character 6 is `C` when the view is clickable. dumpsys uses `app:` as the package of app resources.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/views_dumpsys.txt` (keep the exact 2-space indentation):
```
TASK 42 id=42 userId=0
  ACTIVITY com.example.demo/.MainActivity 7c1d2e3 pid=4321
    Local Activity a1b2c3d State:
      mResumed=true mStopped=false mFinished=false
      mIsChangingConfigurations=false
    ViewRoot:
      mAdded=true mRemoved=false
    View Hierarchy:
      DecorView@5e6f7a8[MainActivity]
        android.widget.LinearLayout{1111111 V.E...... ........ 0,0-1080,2400}
          android.view.ViewStub{2222222 G.E...... ......I. 0,0-0,0 #1020386 android:id/action_mode_bar_stub}
          android.widget.FrameLayout{3333333 V.E...... ........ 0,63-1080,2400 #1020002 android:id/content}
            androidx.constraintlayout.widget.ConstraintLayout{4444444 V.E...... ........ 0,0-1080,2337 #7f0a0123 app:id/root}
              com.google.android.material.appbar.MaterialToolbar{5555555 V.E...... ........ 0,0-1080,147 #7f0a0200 app:id/toolbar}
                androidx.appcompat.widget.AppCompatTextView{6666666 V.ED..... ........ 42,38-300,109}
              androidx.recyclerview.widget.RecyclerView{7777777 VFED..... .F...... 0,147-1080,1500 #7f0a0300 app:id/list}
                androidx.appcompat.widget.AppCompatTextView{8888888 V.ED..C.. ........ 0,0-1080,126 #7f0a0301 app:id/item_title aid=1073741824}
                androidx.appcompat.widget.AppCompatTextView{9999999 V.ED..C.. ........ 0,126-1080,252 #7f0a0301 app:id/item_title aid=1073741825}
              androidx.appcompat.widget.AppCompatImageButton{aaaaaaa V.ED..C.. ........ 960,1520-1050,1610 #7f0a0400 app:id/fab_small}
              android.widget.ProgressBar{bbbbbbb I.ED..... ........ 500,1700-580,1780 #7f0a0500 app:id/progress}
              androidx.compose.ui.platform.ComposeView{ccccccc V.E...... ........ 0,1800-1080,2337 #7f0a0600 app:id/compose_host}
                androidx.compose.ui.platform.AndroidComposeView{ddddddd VFED..... ........ 0,0-1080,537}
                  androidx.compose.ui.platform.AndroidViewsHandler{eeeeeee V.E...... ......ID 0,0-0,0}
    Looper (main, tid 1) {1234abc}
      (Total messages: 0, polling=true, quitting=false)
```

- [ ] **Step 2: Write the failing test**

`tests/test_parse_dumpsys.py`:
```python
from helpers import read_fixture

from layoutcli.model import Rect
from layoutcli.parse.dumpsys import parse_dumpsys


def block(component, resumed, hierarchy):
    lines = [f"  ACTIVITY {component} 1a2b pid=1",
             "    Local Activity 3c4d State:",
             f"      mResumed={'true' if resumed else 'false'} mStopped=false",
             "    View Hierarchy:"]
    lines += ["      " + h for h in hierarchy]
    return "\n".join(lines) + "\n"


def all_nodes(node):
    yield node
    for c in node.children:
        yield from all_nodes(c)


def test_parses_fixture_hierarchy():
    r = parse_dumpsys(read_fixture("views_dumpsys.txt"))
    assert r.package == "com.example.demo"
    assert r.activity == "com.example.demo.MainActivity"
    assert r.root.class_name == "DecorView" and r.root.rel is None
    (linear,) = r.root.children
    assert linear.class_name == "android.widget.LinearLayout"
    stub, content = linear.children
    assert stub.visibility == "gone"
    assert stub.res_id == "android:id/action_mode_bar_stub"
    assert content.rel == Rect(0, 63, 1080, 2400)
    assert len(list(all_nodes(r.root))) == 15


def test_flags_ids_and_aid_suffix():
    r = parse_dumpsys(read_fixture("views_dumpsys.txt"))
    nodes = {n.hash: n for n in all_nodes(r.root)}
    assert nodes["bbbbbbb"].visibility == "invisible"
    assert nodes["8888888"].clickable is True
    assert nodes["8888888"].res_id == "app:id/item_title"
    assert nodes["6666666"].res_id is None and nodes["6666666"].clickable is False


def test_negative_bounds_and_id_without_name():
    text = block("p/.A", True, [
        "DecorView@1[A]",
        "  android.widget.HorizontalScrollView{1a V.E...... ........ -40,0-1120,200}",
        "    android.view.View{2b V.ED..... ........ 0,0-0,0 #7f0a0999}",
    ])
    r = parse_dumpsys(text)
    scroll = r.root.children[0]
    assert scroll.rel == Rect(-40, 0, 1120, 200)
    assert scroll.children[0].res_id is None
    assert scroll.children[0].rel == Rect(0, 0, 0, 0)


def test_prefers_resumed_activity_over_later_ones():
    text = (block("com.a/.Resumed", True, ["DecorView@1[Resumed]"]) +
            block("com.b/com.b.Paused", False, ["DecorView@2[Paused]"]))
    r = parse_dumpsys(text)
    assert (r.package, r.activity) == ("com.a", "com.a.Resumed")


def test_falls_back_to_last_activity_with_hierarchy():
    text = (block("com.a/.One", False, ["DecorView@1[One]"]) +
            block("com.b/.Two", False, ["DecorView@2[Two]"]))
    assert parse_dumpsys(text).activity == "com.b.Two"


def test_returns_none_without_view_hierarchy():
    assert parse_dumpsys("TASK 1 id=1\n  ACTIVITY a/.B 1 pid=2\n") is None
    assert parse_dumpsys("") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_parse_dumpsys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.parse.dumpsys'`

- [ ] **Step 4: Implement `src/layoutcli/parse/dumpsys.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field

from layoutcli.model import Rect

_VIEW_RE = re.compile(
    r"^(?P<cls>[\w.$]+)\{(?P<hash>[0-9a-f]+) (?P<f1>\S+) (?P<f2>\S+) "
    r"(?P<l>-?\d+),(?P<t>-?\d+)-(?P<r>-?\d+),(?P<b>-?\d+)"
    r"(?: #(?P<hexid>[0-9a-f]+)(?: (?P<resname>[^\s}]+))?)?"
    r"[^}]*\}"
)
_ROOT_RE = re.compile(r"^(?P<cls>[\w.$]+)@(?P<hash>[0-9a-f]+)")
_ACTIVITY_RE = re.compile(r"^\s*ACTIVITY (?P<comp>\S+)")


@dataclass(eq=False)
class DNode:
    class_name: str
    hash: str
    flags: str = ""
    rel: Rect | None = None  # relative to parent; None for the DecorView root line
    res_id: str | None = None  # e.g. "app:id/toolbar"
    children: list[DNode] = field(default_factory=list)

    @property
    def visibility(self) -> str:
        return {"I": "invisible", "G": "gone"}.get(self.flags[:1], "visible")

    @property
    def clickable(self) -> bool:
        return len(self.flags) > 6 and self.flags[6] == "C"


@dataclass
class DumpsysResult:
    package: str | None
    activity: str | None
    root: DNode


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_component(comp: str) -> tuple[str | None, str | None]:
    if "/" not in comp:
        return comp, None
    package, activity = comp.split("/", 1)
    if activity.startswith("."):
        activity = package + activity
    return package, activity


def _parse_view_line(s: str) -> DNode | None:
    m = _VIEW_RE.match(s)
    if m:
        return DNode(
            class_name=m["cls"], hash=m["hash"], flags=f"{m['f1']} {m['f2']}",
            rel=Rect(int(m["l"]), int(m["t"]), int(m["r"]), int(m["b"])),
            res_id=m["resname"])
    m = _ROOT_RE.match(s)
    if m:
        return DNode(class_name=m["cls"], hash=m["hash"])
    return None


def _parse_hierarchy(body: list[str]) -> DNode | None:
    if not body:
        return None
    base = _indent(body[0])
    root: DNode | None = None
    stack: list[tuple[int, DNode]] = []
    for line in body:
        node = _parse_view_line(line.strip())
        if node is None:
            continue
        depth = (_indent(line) - base) // 2
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        elif root is None:
            root = node
        else:
            continue  # a second top-level root: ignore it
        stack.append((depth, node))
    return root


def parse_dumpsys(text: str) -> DumpsysResult | None:
    """Parse `dumpsys activity top`; return the resumed activity's View tree."""
    lines = text.splitlines()
    blocks: list[dict] = []
    current: dict | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _ACTIVITY_RE.match(line)
        if m:
            current = {"comp": m["comp"], "resumed": False, "root": None}
            blocks.append(current)
        elif current is not None:
            if "mResumed=true" in line:
                current["resumed"] = True
            elif line.strip() == "View Hierarchy:":
                header = _indent(line)
                j = i + 1
                body = []
                while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > header):
                    if lines[j].strip():
                        body.append(lines[j])
                    j += 1
                current["root"] = _parse_hierarchy(body)
                i = j
                continue
        i += 1
    with_root = [b for b in blocks if b["root"] is not None]
    if not with_root:
        return None
    chosen = next((b for b in with_root if b["resumed"]), with_root[-1])
    package, activity = _split_component(chosen["comp"])
    return DumpsysResult(package, activity, chosen["root"])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_parse_dumpsys.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add src/layoutcli/parse/dumpsys.py tests/test_parse_dumpsys.py tests/fixtures/views_dumpsys.txt
git commit -m "feat: parse dumpsys activity top view hierarchy" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 6: wm parsers and raw device capture

**Files:**
- Create: `src/layoutcli/parse/wm.py`, `src/layoutcli/capture.py`
- Modify: `tests/helpers.py` (append the fakes)
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: `AdbError` (Task 2) and the `Adb.exec_out(cmd, timeout)` / `.serial` shape (Task 3)
- Produces:
  - `parse_wm_size(text) -> tuple[int, int] | None` (the override wins)
  - `parse_wm_density(text) -> int | None` (the override wins)
  - `DUMP_PATH = "/data/local/tmp/layoutcli_dump.xml"`
  - `RawCapture(device={}, dumpsys_text=None, uiautomator_xml=None, screenshot_png=None, wm_size=None, wm_density=None, errors={})`
  - `capture_raw(adb) -> RawCapture`, which never raises `AdbError` and records failures in `errors`. The keys are `"dumpsys"`, `"uiautomator"`, `"screenshot"`, `"wm size"`, `"wm density"` and `"getprop:<key>"`.
  - In `tests/helpers.py`: `PNG_BYTES`, `FakeAdb(responses, serial="emulator-5554")` with a `.calls` list, `views_responses() -> dict[str, bytes | Exception]` and `views_raw() -> RawCapture`

- [ ] **Step 1: Write the failing test**

Append to `tests/helpers.py`:
```python
from layoutcli.capture import DUMP_PATH, RawCapture

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeAdb:
    def __init__(self, responses: dict, serial: str = "emulator-5554"):
        self.responses = responses
        self.serial = serial
        self.calls: list[str] = []

    def exec_out(self, cmd: str, timeout: float = 30.0) -> bytes:
        self.calls.append(cmd)
        response = self.responses.get(cmd, b"")
        if isinstance(response, Exception):
            raise response
        return response


def views_responses() -> dict:
    return {
        "getprop ro.product.model": b"Pixel 7\n",
        "getprop ro.build.version.sdk": b"34\n",
        "wm size": b"Physical size: 1080x2400\n",
        "wm density": b"Physical density: 420\n",
        "dumpsys activity top": (FIXTURES / "views_dumpsys.txt").read_bytes(),
        f"uiautomator dump {DUMP_PATH}": f"UI hierchary dumped to: {DUMP_PATH}\n".encode(),
        f"cat {DUMP_PATH}": (FIXTURES / "views_uiautomator.xml").read_bytes(),
        f"rm -f {DUMP_PATH}": b"",
        "screencap -p": PNG_BYTES,
    }


def views_raw() -> RawCapture:
    return RawCapture(
        device={"serial": "emulator-5554", "model": "Pixel 7", "sdk": "34"},
        dumpsys_text=read_fixture("views_dumpsys.txt"),
        uiautomator_xml=read_fixture("views_uiautomator.xml"),
        screenshot_png=PNG_BYTES,
        wm_size="Physical size: 1080x2400\n",
        wm_density="Physical density: 420\n",
    )
```

`tests/test_capture.py`:
```python
from helpers import PNG_BYTES, FakeAdb, views_responses

from layoutcli.adb import AdbError
from layoutcli.capture import DUMP_PATH, capture_raw
from layoutcli.parse.wm import parse_wm_density, parse_wm_size


def test_wm_parsers_prefer_override():
    assert parse_wm_size("Physical size: 1080x2400\nOverride size: 720x1600\n") == (720, 1600)
    assert parse_wm_size("Physical size: 1080x2400\n") == (1080, 2400)
    assert parse_wm_size("garbage") is None
    assert parse_wm_density("Physical density: 420\nOverride density: 480\n") == 480
    assert parse_wm_density("") is None


def test_capture_collects_all_sources():
    adb = FakeAdb(views_responses())
    raw = capture_raw(adb)
    assert raw.device == {"serial": "emulator-5554", "model": "Pixel 7", "sdk": "34"}
    assert "View Hierarchy:" in raw.dumpsys_text
    assert raw.uiautomator_xml.startswith("<?xml")
    assert raw.screenshot_png == PNG_BYTES
    assert raw.wm_size.startswith("Physical size")
    assert raw.errors == {}
    assert f"rm -f {DUMP_PATH}" in adb.calls


def test_uiautomator_idle_failure_is_recorded_not_raised():
    responses = views_responses()
    responses[f"uiautomator dump {DUMP_PATH}"] = b"ERROR: could not get idle state.\n"
    raw = capture_raw(FakeAdb(responses))
    assert raw.uiautomator_xml is None
    assert raw.errors["uiautomator"] == "uiautomator dump failed: ERROR: could not get idle state."
    assert raw.dumpsys_text is not None
    assert raw.screenshot_png == PNG_BYTES


def test_adb_failure_in_one_source_does_not_stop_others():
    responses = views_responses()
    responses["dumpsys activity top"] = AdbError("boom")
    raw = capture_raw(FakeAdb(responses))
    assert raw.dumpsys_text is None
    assert raw.errors["dumpsys"] == "boom"
    assert raw.uiautomator_xml is not None


def test_screencap_without_png_signature_is_an_error():
    responses = views_responses()
    responses["screencap -p"] = b"Error: capture failed\n"
    raw = capture_raw(FakeAdb(responses))
    assert raw.screenshot_png is None
    assert "no PNG" in raw.errors["screenshot"]


def test_crlf_output_is_normalised():
    responses = views_responses()
    responses["dumpsys activity top"] = responses["dumpsys activity top"].replace(b"\n", b"\r\n")
    raw = capture_raw(FakeAdb(responses))
    assert "\r" not in raw.dumpsys_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_capture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.capture'`

- [ ] **Step 3: Implement `src/layoutcli/parse/wm.py` and `src/layoutcli/capture.py`**

`src/layoutcli/parse/wm.py`:
```python
from __future__ import annotations

import re

_SIZE_RE = re.compile(r"(Physical|Override) size: (\d+)x(\d+)")
_DENSITY_RE = re.compile(r"(Physical|Override) density: (\d+)")


def parse_wm_size(text: str) -> tuple[int, int] | None:
    found = {m[1]: (int(m[2]), int(m[3])) for m in _SIZE_RE.finditer(text)}
    return found.get("Override") or found.get("Physical")


def parse_wm_density(text: str) -> int | None:
    found = {m[1]: int(m[2]) for m in _DENSITY_RE.finditer(text)}
    return found.get("Override") or found.get("Physical")
```

`src/layoutcli/capture.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from layoutcli.adb import AdbError

DUMP_PATH = "/data/local/tmp/layoutcli_dump.xml"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class DeviceShell(Protocol):
    serial: str

    def exec_out(self, cmd: str, timeout: float = 30.0) -> bytes: ...


@dataclass
class RawCapture:
    device: dict[str, str] = field(default_factory=dict)
    dumpsys_text: str | None = None
    uiautomator_xml: str | None = None
    screenshot_png: bytes | None = None
    wm_size: str | None = None
    wm_density: str | None = None
    errors: dict[str, str] = field(default_factory=dict)


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace").replace("\r\n", "\n")


def _dump_uiautomator(adb: DeviceShell) -> str:
    out = _text(adb.exec_out(f"uiautomator dump {DUMP_PATH}", timeout=60.0))
    if "dumped to" not in out:
        raise AdbError(f"uiautomator dump failed: {out.strip() or 'no output'}")
    xml = _text(adb.exec_out(f"cat {DUMP_PATH}"))
    try:
        adb.exec_out(f"rm -f {DUMP_PATH}")
    except AdbError:
        pass
    return xml


def _screenshot(adb: DeviceShell) -> bytes:
    png = adb.exec_out("screencap -p", timeout=30.0)
    if not png.startswith(PNG_SIGNATURE):
        raise AdbError("screencap returned no PNG data")
    return png


def capture_raw(adb: DeviceShell) -> RawCapture:
    """Run every capture step; record failures in `errors` instead of raising."""
    raw = RawCapture(device={"serial": adb.serial})

    def attempt(key: str, fn):
        try:
            return fn()
        except AdbError as e:
            raw.errors[key] = str(e)
            return None

    for key, prop in (("model", "ro.product.model"), ("sdk", "ro.build.version.sdk")):
        value = attempt(f"getprop:{key}", lambda p=prop: _text(adb.exec_out(f"getprop {p}")).strip())
        if value:
            raw.device[key] = value
    raw.wm_size = attempt("wm size", lambda: _text(adb.exec_out("wm size")))
    raw.wm_density = attempt("wm density", lambda: _text(adb.exec_out("wm density")))
    raw.dumpsys_text = attempt("dumpsys", lambda: _text(adb.exec_out("dumpsys activity top")))
    raw.uiautomator_xml = attempt("uiautomator", lambda: _dump_uiautomator(adb))
    raw.screenshot_png = attempt("screenshot", lambda: _screenshot(adb))
    return raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_capture.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/parse/wm.py src/layoutcli/capture.py tests/helpers.py tests/test_capture.py
git commit -m "feat: raw device capture with per-source error recording" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 7: Merge dumpsys and uiautomator trees

**Files:**
- Create: `src/layoutcli/merge.py`
- Test: `tests/test_merge.py`

**Interfaces:**
- Consumes: `DNode`, `DumpsysResult` (Task 5), `parse_uiautomator` (Task 4), `ViewNode`, `Rect`, `short_id` (Task 1)
- Produces:
  - `MergeError(Exception)`
  - `pick_window(roots: list[ViewNode], package: str | None) -> ViewNode | None`
  - `match_children(dkids: list[DNode], ukids: list[ViewNode]) -> tuple[list[tuple[DNode, ViewNode | None]], list[ViewNode]]`
  - `merge(dump: DumpsysResult | None, ui_roots: list[ViewNode]) -> ViewNode`

Merge rules:
- The dumpsys tree is the backbone and supplies the real class names, GONE/INVISIBLE views and relative bounds.
- uiautomator children are matched to dumpsys children in order (a greedy subsequence). A pair matches when the dumpsys child is visible, the short ids are equal (both `None` counts as equal), and the uiautomator size is no larger than the dumpsys size. uiautomator bounds are clipped, so it can be smaller.
- Absolute bounds are the parent's absolute origin plus the relative bounds. When a uiautomator match has exactly the same size, its bounds win. This corrects for scroll offsets, and the corrected position propagates to the children.
- The root origin is the top-left of the uiautomator root (`(0, 0)` if there isn't one).
- Under an `...AndroidComposeView`, every uiautomator child is appended as a Compose semantics node with `sources == ["uiautomator"]`. Elsewhere, unmatched uiautomator children are appended after the matched ones.
- Merged nodes get `sources = ["dumpsys"]` or `["dumpsys", "uiautomator"]`. `props["dumpsys"]` holds hash, flags, rel_bounds and resource_id, and `props["uiautomator"]` copies the XML attributes.

- [ ] **Step 1: Write the failing test**

`tests/test_merge.py`:
```python
import pytest
from helpers import find_by_id, read_fixture

from layoutcli.merge import MergeError, merge, pick_window
from layoutcli.model import Rect, ViewNode
from layoutcli.parse.dumpsys import DNode, DumpsysResult, parse_dumpsys
from layoutcli.parse.uiautomator import parse_uiautomator


def fixture_merge():
    return merge(parse_dumpsys(read_fixture("views_dumpsys.txt")),
                 parse_uiautomator(read_fixture("views_uiautomator.xml")))


def test_fixture_merge_uses_real_classes_and_absolute_bounds():
    root = fixture_merge()
    assert root.class_name == "DecorView"
    assert root.bounds == Rect(0, 0, 1080, 2400)
    toolbar = find_by_id(root, "toolbar")
    assert toolbar.class_name == "com.google.android.material.appbar.MaterialToolbar"
    assert toolbar.bounds == Rect(0, 63, 1080, 210)
    assert toolbar.sources == ["dumpsys", "uiautomator"]
    assert toolbar.props["dumpsys"]["hash"] == "5555555"
    assert toolbar.props["uiautomator"]["class"] == "android.view.ViewGroup"
    assert toolbar.children[0].text == "Demo"


def test_hidden_views_come_from_dumpsys_only():
    root = fixture_merge()
    progress = find_by_id(root, "progress")
    assert progress.visibility == "invisible"
    assert progress.sources == ["dumpsys"]
    assert progress.bounds == Rect(500, 1763, 580, 1843)
    assert find_by_id(root, "action_mode_bar_stub").visibility == "gone"


def test_repeated_ids_match_in_order():
    items = [n for n, _ in fixture_merge().walk() if n.id == "item_title"]
    assert [i.text for i in items] == ["First item", "Second item"]
    assert items[1].bounds == Rect(0, 336, 1080, 462)


def test_compose_semantics_nodes_hang_under_compose_view():
    root = fixture_merge()
    host = find_by_id(root, "compose_host").children[0]
    assert host.class_name == "androidx.compose.ui.platform.AndroidComposeView"
    assert [c.class_name for c in host.children] == [
        "androidx.compose.ui.platform.AndroidViewsHandler",
        "android.widget.TextView", "android.widget.Button"]
    assert host.children[1].text == "Hello Compose"
    ui_only = [n for n, _ in root.walk() if n.sources == ["uiautomator"]]
    assert len(ui_only) == 3
    assert sum(1 for _ in root.walk()) == 18


def test_scrolled_children_take_uiautomator_position_and_propagate():
    leaf = DNode("android.widget.ImageView", "4", "V.ED..... ........", Rect(10, 10, 20, 20))
    item = DNode("android.widget.LinearLayout", "3", "V.E...... ........", Rect(0, 100, 100, 150), children=[leaf])
    lst = DNode("androidx.recyclerview.widget.RecyclerView", "2", "VFED..... ........",
                Rect(0, 0, 100, 200), "app:id/list", [item])
    droot = DNode("DecorView", "1", children=[lst])
    u_item = ViewNode("android.widget.LinearLayout", bounds=Rect(0, 80, 100, 130),
                      sources=["uiautomator"], props={"uiautomator": {}})
    u_list = ViewNode("androidx.recyclerview.widget.RecyclerView", id="list", bounds=Rect(0, 0, 100, 200),
                      sources=["uiautomator"], props={"uiautomator": {}}, children=[u_item])
    u_root = ViewNode("android.widget.FrameLayout", bounds=Rect(0, 0, 100, 200),
                      sources=["uiautomator"], props={"uiautomator": {}}, children=[u_list])
    merged = merge(DumpsysResult("p", "p.A", droot), [u_root])
    m_item = merged.children[0].children[0]
    assert m_item.bounds == Rect(0, 80, 100, 130)
    assert m_item.children[0].bounds == Rect(10, 90, 20, 100)


def test_dumpsys_only_assumes_origin_zero():
    root = merge(parse_dumpsys(read_fixture("views_dumpsys.txt")), [])
    assert find_by_id(root, "toolbar").bounds == Rect(0, 63, 1080, 210)
    assert root.bounds == Rect(0, 0, 1080, 2400)
    assert all(n.sources == ["dumpsys"] for n, _ in root.walk())


def test_uiautomator_only_returns_window_for_package():
    a = ViewNode("a.A", props={"uiautomator": {"package": "com.ime"}})
    b = ViewNode("b.B", props={"uiautomator": {"package": "com.app"}})
    assert pick_window([a, b], "com.app") is b
    assert pick_window([a, b], None) is a
    assert merge(None, [a, b]) is a


def test_nothing_to_merge_raises():
    with pytest.raises(MergeError):
        merge(None, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_merge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.merge'`

- [ ] **Step 3: Implement `src/layoutcli/merge.py`**

```python
from __future__ import annotations

from layoutcli.model import Rect, ViewNode, short_id
from layoutcli.parse.dumpsys import DNode, DumpsysResult

COMPOSE_HOST_SUFFIX = "AndroidComposeView"


class MergeError(Exception):
    pass


def pick_window(roots: list[ViewNode], package: str | None) -> ViewNode | None:
    if not roots:
        return None
    if package:
        for root in roots:
            if root.props.get("uiautomator", {}).get("package") == package:
                return root
    return roots[0]


def merge(dump: DumpsysResult | None, ui_roots: list[ViewNode]) -> ViewNode:
    ui = pick_window(ui_roots, dump.package if dump else None)
    if dump is None:
        if ui is None:
            raise MergeError("no view hierarchy available")
        return ui
    origin = (ui.bounds.left, ui.bounds.top) if ui is not None and ui.bounds is not None else (0, 0)
    return _convert(dump.root, ui, origin)


def _compatible(d: DNode, u: ViewNode) -> bool:
    if d.visibility != "visible" or short_id(d.res_id) != u.id:
        return False
    if d.rel is None or u.bounds is None:
        return True
    return u.bounds.width <= d.rel.width and u.bounds.height <= d.rel.height


def match_children(dkids: list[DNode], ukids: list[ViewNode]
                   ) -> tuple[list[tuple[DNode, ViewNode | None]], list[ViewNode]]:
    pairs: list[tuple[DNode, ViewNode | None]] = []
    used: set[int] = set()
    pos = 0
    for d in dkids:
        found = next((k for k in range(pos, len(ukids)) if _compatible(d, ukids[k])), None)
        if found is None:
            pairs.append((d, None))
        else:
            pairs.append((d, ukids[found]))
            used.add(found)
            pos = found + 1
    leftovers = [u for k, u in enumerate(ukids) if k not in used]
    return pairs, leftovers


def _children_extent(dn: DNode) -> Rect:
    rects = [c.rel for c in dn.children if c.rel is not None]
    if not rects:
        return Rect(0, 0, 0, 0)
    return Rect(0, 0, max(r.right for r in rects), max(r.bottom for r in rects))


def _dumpsys_props(dn: DNode) -> dict[str, str]:
    props = {"hash": dn.hash, "flags": dn.flags,
             "rel_bounds": str(dn.rel) if dn.rel else "", "resource_id": dn.res_id or ""}
    return {k: v for k, v in props.items() if v}


def _convert(dn: DNode, un: ViewNode | None, origin: tuple[int, int]) -> ViewNode:
    if dn.rel is not None:
        bounds = dn.rel.offset(*origin)
    elif un is not None and un.bounds is not None:
        bounds = un.bounds
    else:
        bounds = _children_extent(dn).offset(*origin)
    if un is not None and un.bounds is not None and un.bounds.size == bounds.size:
        bounds = un.bounds  # uiautomator knows scroll offsets; dumpsys does not

    props = {"dumpsys": _dumpsys_props(dn)}
    sources = ["dumpsys"]
    if un is not None:
        props["uiautomator"] = dict(un.props.get("uiautomator", {}))
        sources.append("uiautomator")
    node = ViewNode(class_name=dn.class_name, id=short_id(dn.res_id), bounds=bounds,
                    visibility=dn.visibility, text=un.text if un else None,
                    sources=sources, props=props)

    child_origin = (bounds.left, bounds.top)
    u_children = un.children if un is not None else []
    if un is not None and dn.class_name.endswith(COMPOSE_HOST_SUFFIX):
        node.children = [_convert(c, None, child_origin) for c in dn.children] + list(u_children)
        return node
    pairs, leftovers = match_children(dn.children, u_children)
    node.children = [_convert(d, u, child_origin) for d, u in pairs] + leftovers
    return node
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_merge.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/merge.py tests/test_merge.py
git commit -m "feat: merge dumpsys view tree with uiautomator semantics" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 8: Build the snapshot, then save and load it

**Files:**
- Create: `src/layoutcli/build.py`, `src/layoutcli/snapshot_io.py`
- Modify: `tests/helpers.py` (append `views_snapshot`)
- Test: `tests/test_build_io.py`

**Interfaces:**
- Consumes: `RawCapture` (Task 6), `parse_dumpsys` (5), `parse_uiautomator` (4), `parse_wm_size`, `parse_wm_density` (6), `merge` (7) and `Snapshot` (1)
- Produces:
  - `BuildError(Exception)`
  - `build_snapshot(raw: RawCapture, captured_at: str) -> Snapshot`. Capabilities have the keys `dumpsys`, `uiautomator` and `screenshot`, each with the value `"ok"` or a reason.
  - `SnapshotError(Exception)`
  - `save_capture(raw, snap, out_dir: Path) -> None`, which writes `snapshot.json`, `screen.png`, `raw/dumpsys.txt`, `raw/uiautomator.xml`, `raw/wm_size.txt`, `raw/wm_density.txt` and `raw/capture.json`, and sets `snap.screenshot`
  - `load_snapshot(path: Path) -> Snapshot`, where `path` is a directory or a `snapshot.json` file
  - In `tests/helpers.py`: `views_snapshot() -> Snapshot`

- [ ] **Step 1: Write the failing test**

Append to `tests/helpers.py`:
```python
from layoutcli.build import build_snapshot


def views_snapshot():
    return build_snapshot(views_raw(), captured_at="2026-10-04T10:00:00+00:00")
```

`tests/test_build_io.py`:
```python
import pytest
from helpers import PNG_BYTES, find_by_id, views_raw, views_snapshot

from layoutcli.build import BuildError, build_snapshot
from layoutcli.model import Rect
from layoutcli.snapshot_io import SnapshotError, load_snapshot, save_capture

AT = "2026-10-04T10:00:00+00:00"


def test_build_snapshot_from_full_capture():
    snap = views_snapshot()
    assert snap.package == "com.example.demo"
    assert snap.activity == "com.example.demo.MainActivity"
    assert snap.screen == (1080, 2400)
    assert snap.density == 420
    assert snap.capabilities == {"dumpsys": "ok", "uiautomator": "ok", "screenshot": "ok"}
    assert snap.device["model"] == "Pixel 7"
    assert snap.captured_at == AT


def test_falls_back_to_dumpsys_when_uiautomator_failed():
    raw = views_raw()
    raw.uiautomator_xml = None
    raw.errors["uiautomator"] = "uiautomator dump failed: ERROR: could not get idle state."
    snap = build_snapshot(raw, AT)
    assert snap.capabilities["uiautomator"].endswith("could not get idle state.")
    assert find_by_id(snap.root, "toolbar").bounds == Rect(0, 63, 1080, 210)


def test_uiautomator_only_capture():
    raw = views_raw()
    raw.dumpsys_text = None
    raw.errors["dumpsys"] = "boom"
    snap = build_snapshot(raw, AT)
    assert snap.root.class_name == "android.widget.FrameLayout"
    assert snap.package == "com.example.demo"
    assert snap.activity is None
    assert snap.capabilities["dumpsys"] == "boom"


def test_malformed_uiautomator_xml_is_reported():
    raw = views_raw()
    raw.uiautomator_xml = "<hierarchy><node"
    snap = build_snapshot(raw, AT)
    assert snap.capabilities["uiautomator"].startswith("invalid XML")
    assert snap.root.class_name == "DecorView"


def test_screen_size_falls_back_to_root_bounds():
    raw = views_raw()
    raw.wm_size = None
    assert build_snapshot(raw, AT).screen == (1080, 2400)


def test_nothing_captured_raises():
    raw = views_raw()
    raw.dumpsys_text = None
    raw.uiautomator_xml = None
    raw.errors = {"dumpsys": "boom", "uiautomator": "no device"}
    with pytest.raises(BuildError, match="boom"):
        build_snapshot(raw, AT)


def test_save_and_load_round_trip(tmp_path):
    raw = views_raw()
    snap = build_snapshot(raw, AT)
    out = tmp_path / "s"
    save_capture(raw, snap, out)
    assert (out / "screen.png").read_bytes() == PNG_BYTES
    assert (out / "raw" / "dumpsys.txt").is_file()
    assert (out / "raw" / "uiautomator.xml").is_file()
    loaded = load_snapshot(out)
    assert loaded.screenshot == "screen.png"
    assert loaded.to_dict() == snap.to_dict()
    assert load_snapshot(out / "snapshot.json").package == "com.example.demo"


def test_unicode_text_survives_save_and_load(tmp_path):
    raw = views_raw()
    snap = build_snapshot(raw, AT)
    find_by_id(snap.root, "toolbar").children[0].text = "Привет 👋"
    save_capture(raw, snap, tmp_path)
    assert find_by_id(load_snapshot(tmp_path).root, "toolbar").children[0].text == "Привет 👋"


def test_load_rejects_non_snapshot_dir(tmp_path):
    with pytest.raises(SnapshotError, match="not a layoutcli snapshot"):
        load_snapshot(tmp_path)


def test_load_rejects_corrupt_json(tmp_path):
    (tmp_path / "snapshot.json").write_text("{", encoding="utf-8")
    with pytest.raises(SnapshotError, match="cannot read"):
        load_snapshot(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_io.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.build'`

- [ ] **Step 3: Implement `src/layoutcli/build.py` and `src/layoutcli/snapshot_io.py`**

`src/layoutcli/build.py`:
```python
from __future__ import annotations

import xml.etree.ElementTree as ET

from layoutcli.capture import RawCapture
from layoutcli.merge import merge
from layoutcli.model import Snapshot
from layoutcli.parse.dumpsys import parse_dumpsys
from layoutcli.parse.uiautomator import parse_uiautomator
from layoutcli.parse.wm import parse_wm_density, parse_wm_size

DEFAULT_SCREEN = (1080, 1920)


class BuildError(Exception):
    pass


def build_snapshot(raw: RawCapture, captured_at: str) -> Snapshot:
    caps: dict[str, str] = {}

    dump = parse_dumpsys(raw.dumpsys_text) if raw.dumpsys_text else None
    caps["dumpsys"] = "ok" if dump else raw.errors.get("dumpsys", "no activity view hierarchy found")

    ui_roots = []
    if raw.uiautomator_xml:
        try:
            ui_roots = parse_uiautomator(raw.uiautomator_xml)
            caps["uiautomator"] = "ok" if ui_roots else "empty hierarchy"
        except ET.ParseError as e:
            caps["uiautomator"] = f"invalid XML: {e}"
    else:
        caps["uiautomator"] = raw.errors.get("uiautomator", "not captured")

    caps["screenshot"] = "ok" if raw.screenshot_png else raw.errors.get("screenshot", "not captured")

    if dump is None and not ui_roots:
        raise BuildError("no view hierarchy captured: "
                         f"dumpsys: {caps['dumpsys']}; uiautomator: {caps['uiautomator']}")

    root = merge(dump, ui_roots)
    screen = parse_wm_size(raw.wm_size or "")
    if screen is None:
        screen = root.bounds.size if root.bounds and root.bounds.width > 0 else DEFAULT_SCREEN
    package = dump.package if dump else root.props.get("uiautomator", {}).get("package")
    return Snapshot(
        root=root, screen=screen, density=parse_wm_density(raw.wm_density or ""),
        package=package, activity=dump.activity if dump else None,
        device=dict(raw.device), captured_at=captured_at, capabilities=caps)
```

`src/layoutcli/snapshot_io.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

from layoutcli.capture import RawCapture
from layoutcli.model import Snapshot

SNAPSHOT_FILE = "snapshot.json"
SCREEN_FILE = "screen.png"


class SnapshotError(Exception):
    pass


def save_capture(raw: RawCapture, snap: Snapshot, out_dir: Path) -> None:
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, content in (("dumpsys.txt", raw.dumpsys_text), ("uiautomator.xml", raw.uiautomator_xml),
                          ("wm_size.txt", raw.wm_size), ("wm_density.txt", raw.wm_density)):
        if content is not None:
            (raw_dir / name).write_text(content, encoding="utf-8")
    (raw_dir / "capture.json").write_text(
        json.dumps({"device": raw.device, "errors": raw.errors}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    if raw.screenshot_png:
        (out_dir / SCREEN_FILE).write_bytes(raw.screenshot_png)
        snap.screenshot = SCREEN_FILE
    (out_dir / SNAPSHOT_FILE).write_text(
        json.dumps(snap.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")


def load_snapshot(path: Path) -> Snapshot:
    file = path / SNAPSHOT_FILE if path.is_dir() else path
    if not file.is_file():
        raise SnapshotError(f"{path} is not a layoutcli snapshot (no {SNAPSHOT_FILE})")
    try:
        return Snapshot.from_dict(json.loads(file.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError, IndexError) as e:
        raise SnapshotError(f"cannot read {file}: {e}") from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_build_io.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/build.py src/layoutcli/snapshot_io.py tests/helpers.py tests/test_build_io.py
git commit -m "feat: build snapshots with capability reporting; save/load snapshot dirs" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 9: Wireframe renderer

**Files:**
- Create: `src/layoutcli/wireframe.py`
- Test: `tests/test_wireframe.py`

**Interfaces:**
- Consumes: `ViewNode`, `Rect` (Task 1)
- Produces:
  - `visible_nodes(root) -> Iterator[ViewNode]`, a pre-order walk that prunes non-visible subtrees
  - `render_wireframe(root, screen: tuple[int, int], cols: int, rows: int, selected: ViewNode | None = None) -> rich.text.Text`, which returns exactly `rows` lines of `cols` characters each, or an empty Text when a dimension is ≤ 0

Scaling: terminal cells are about twice as tall as they are wide, so `scale = min(cols/sw, 2*rows/sh)` with `sx = scale` and `sy = scale/2`. Unselected boxes are drawn in `grey50`. The selected box is drawn last in `bold yellow`, with its short class name written into the top border.

- [ ] **Step 1: Write the failing test**

`tests/test_wireframe.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_wireframe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.wireframe'`

- [ ] **Step 3: Implement `src/layoutcli/wireframe.py`**

```python
from __future__ import annotations

import math
from typing import Iterator

from rich.text import Text

from layoutcli.model import Rect, ViewNode

FRAME_STYLE = "grey50"
SELECTED_STYLE = "bold yellow"
_EPS = 1e-9


def visible_nodes(root: ViewNode) -> Iterator[ViewNode]:
    if root.visibility != "visible":
        return
    yield root
    for child in root.children:
        yield from visible_nodes(child)


class _Canvas:
    def __init__(self, cols: int, rows: int, sx: float, sy: float):
        self.cols, self.rows, self.sx, self.sy = cols, rows, sx, sy
        self.chars = [[" "] * cols for _ in range(rows)]
        self.styles: list[list[str]] = [[""] * cols for _ in range(rows)]

    def put(self, x: int, y: int, ch: str, style: str) -> None:
        if 0 <= x < self.cols and 0 <= y < self.rows:
            self.chars[y][x] = ch
            self.styles[y][x] = style

    def box(self, r: Rect, style: str, label: str | None = None) -> None:
        x0 = math.floor(r.left * self.sx + _EPS)
        y0 = math.floor(r.top * self.sy + _EPS)
        x1 = max(x0, math.ceil(r.right * self.sx - _EPS) - 1)
        y1 = max(y0, math.ceil(r.bottom * self.sy - _EPS) - 1)
        for x in range(max(x0, 0), min(x1, self.cols - 1) + 1):
            self.put(x, y0, "─", style)
            self.put(x, y1, "─", style)
        for y in range(max(y0, 0), min(y1, self.rows - 1) + 1):
            self.put(x0, y, "│", style)
            self.put(x1, y, "│", style)
        if x1 > x0 and y1 > y0:
            self.put(x0, y0, "┌", style)
            self.put(x1, y0, "┐", style)
            self.put(x0, y1, "└", style)
            self.put(x1, y1, "┘", style)
        if label and x1 - x0 > 1:
            for i, ch in enumerate(label[: x1 - x0 - 1]):
                self.put(x0 + 1 + i, y0, ch, style)

    def to_text(self) -> Text:
        text = Text()
        for y in range(self.rows):
            if y:
                text.append("\n")
            chars, styles = self.chars[y], self.styles[y]
            start = 0
            for x in range(1, self.cols + 1):
                if x == self.cols or styles[x] != styles[start]:
                    text.append("".join(chars[start:x]), style=styles[start])
                    start = x
        return text


def render_wireframe(root: ViewNode, screen: tuple[int, int], cols: int, rows: int,
                     selected: ViewNode | None = None) -> Text:
    sw, sh = screen
    if cols <= 0 or rows <= 0 or sw <= 0 or sh <= 0:
        return Text("")
    scale = min(cols / sw, 2 * rows / sh)
    canvas = _Canvas(cols, rows, scale, scale / 2)
    for node in visible_nodes(root):
        if node is not selected and node.bounds is not None:
            canvas.box(node.bounds, FRAME_STYLE)
    if selected is not None and selected.bounds is not None:
        canvas.box(selected.bounds, SELECTED_STYLE, label=selected.short_class)
    return canvas.to_text()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_wireframe.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/wireframe.py tests/test_wireframe.py
git commit -m "feat: scaled box-drawing wireframe renderer" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 10: Display formatting and the TUI

**Files:**
- Create: `src/layoutcli/format.py`, `src/layoutcli/tui/__init__.py` (empty), `src/layoutcli/tui/app.py`
- Test: `tests/test_format.py`, `tests/test_tui.py`

**Interfaces:**
- Consumes: `Snapshot`, `ViewNode`, `Rect` (Task 1), `render_wireframe` (Task 9) and `views_snapshot()` (the Task 8 helper)
- Produces:
  - `dp(px: int, density: int | None) -> int | None`
  - `size_text(rect: Rect, density: int | None) -> str`
  - `node_label(node: ViewNode) -> rich.text.Text`
  - `node_rows(node: ViewNode, density: int | None) -> list[tuple[str, str, str]]`
  - `LayoutApp(snapshot)`, a textual `App` with a `.selected: ViewNode | None` attribute. Its widgets are `#tree` (`Tree`), `#props` (`DataTable`) and `#wire` (`Wireframe`).
  - `Wireframe(snapshot)`, a widget with `.selected` and `.select(node)`

- [ ] **Step 1: Write the failing tests**

`tests/test_format.py`:
```python
from layoutcli.format import dp, node_label, node_rows, size_text
from layoutcli.model import Rect, ViewNode


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
```

`tests/test_tui.py`:
```python
import asyncio

from helpers import views_snapshot
from textual.widgets import DataTable, Tree

from layoutcli.tui.app import LayoutApp


def tree_nodes(node):
    yield node
    for child in node.children:
        yield from tree_nodes(child)


def test_selecting_a_node_updates_properties_and_wireframe():
    async def scenario():
        app = LayoutApp(views_snapshot())
        async with app.run_test(size=(140, 45)) as pilot:
            tree = app.query_one("#tree", Tree)
            target = next(n for n in tree_nodes(tree.root) if n.data is not None and n.data.id == "toolbar")
            tree.move_cursor(target)
            await pilot.pause()
            assert app.selected is target.data
            table = app.query_one("#props", DataTable)
            values = [str(table.get_row_at(i)[2]) for i in range(table.row_count)]
            assert "com.google.android.material.appbar.MaterialToolbar" in values
            wire = app.query_one("#wire")
            assert wire.selected is target.data
            assert "MaterialToolbar"[:5] in wire.render().plain

    asyncio.run(scenario())


def test_tree_contains_every_node():
    async def scenario():
        snap = views_snapshot()
        app = LayoutApp(snap)
        async with app.run_test(size=(140, 45)):
            tree = app.query_one("#tree", Tree)
            assert sum(1 for _ in tree_nodes(tree.root)) == sum(1 for _ in snap.root.walk())

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_format.py tests/test_tui.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'layoutcli.format'`

- [ ] **Step 3: Implement `src/layoutcli/format.py`**

```python
from __future__ import annotations

from rich.text import Text

from layoutcli.model import Rect, ViewNode


def dp(px: int, density: int | None) -> int | None:
    return round(px * 160 / density) if density else None


def size_text(rect: Rect, density: int | None) -> str:
    text = f"{rect.width}x{rect.height}px"
    if density:
        text += f" ({dp(rect.width, density)}x{dp(rect.height, density)}dp)"
    return text


def _clip(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def node_label(node: ViewNode) -> Text:
    label = Text(node.short_class, style="bold")
    if node.id:
        label.append(f" #{node.id}", style="cyan")
    if node.text:
        label.append(f' "{_clip(node.text, 30)}"', style="green")
    if node.bounds is not None:
        label.append(f" {node.bounds.width}x{node.bounds.height}", style="dim")
    if node.visibility != "visible":
        label.append(f" [{node.visibility}]", style="red")
        label.stylize("dim")
    if node.sources == ["uiautomator"]:
        label.append(" ◇", style="magenta")
    return label


def node_rows(node: ViewNode, density: int | None) -> list[tuple[str, str, str]]:
    rows = [("view", "class", node.class_name),
            ("view", "id", node.id or "—"),
            ("view", "visibility", node.visibility)]
    if node.bounds is not None:
        rows.append(("view", "bounds", str(node.bounds)))
        rows.append(("view", "size", size_text(node.bounds, density)))
    if node.text:
        rows.append(("view", "text", node.text))
    rows.append(("view", "sources", ", ".join(node.sources)))
    for source in sorted(node.props):
        for key, value in node.props[source].items():
            rows.append((source, key, value))
    return rows
```

- [ ] **Step 4: Implement `src/layoutcli/tui/app.py`**

Also create an empty `src/layoutcli/tui/__init__.py`.

```python
from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, Tree
from textual.widgets.tree import TreeNode

from layoutcli.format import node_label, node_rows
from layoutcli.model import Snapshot, ViewNode
from layoutcli.wireframe import render_wireframe


class Wireframe(Widget):
    def __init__(self, snapshot: Snapshot, **kwargs):
        super().__init__(**kwargs)
        self.snapshot = snapshot
        self.selected: ViewNode | None = None

    def select(self, node: ViewNode | None) -> None:
        self.selected = node
        self.refresh()

    def render(self) -> Text:
        size = self.content_size
        return render_wireframe(self.snapshot.root, self.snapshot.screen,
                                size.width, size.height, self.selected)


def _add_children(tree_node: TreeNode[ViewNode], view: ViewNode) -> None:
    for child in view.children:
        if child.children:
            branch = tree_node.add(node_label(child), data=child, expand=True)
            _add_children(branch, child)
        else:
            tree_node.add_leaf(node_label(child), data=child)


class LayoutApp(App):
    TITLE = "layoutcli"
    CSS = """
    #tree { width: 1fr; }
    #right { width: 1fr; }
    #props { height: 2fr; }
    #wire { height: 3fr; border: round $primary; }
    """
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, snapshot: Snapshot):
        super().__init__()
        self.snapshot = snapshot
        self.selected: ViewNode | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Tree(node_label(self.snapshot.root), data=self.snapshot.root, id="tree")
            with Vertical(id="right"):
                yield DataTable(id="props", cursor_type="row", zebra_stripes=True)
                yield Wireframe(self.snapshot, id="wire")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.snapshot.activity or self.snapshot.package or ""
        self.query_one("#props", DataTable).add_columns("source", "property", "value")
        tree = self.query_one("#tree", Tree)
        _add_children(tree.root, self.snapshot.root)
        tree.root.expand()
        tree.focus()
        self.select(self.snapshot.root)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        if event.node.data is not None:
            self.select(event.node.data)

    def select(self, node: ViewNode) -> None:
        self.selected = node
        table = self.query_one("#props", DataTable)
        table.clear()
        for row in node_rows(node, self.snapshot.density):
            table.add_row(*row)
        self.query_one("#wire", Wireframe).select(node)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_format.py tests/test_tui.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add src/layoutcli/format.py src/layoutcli/tui tests/test_format.py tests/test_tui.py
git commit -m "feat: textual TUI with hierarchy tree, properties and wireframe" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 11: CLI commands and README

**Files:**
- Create: `src/layoutcli/cli.py`, `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `find_adb`, `Adb`, `AdbError` (Tasks 2–3), `capture_raw` (6), `build_snapshot`, `BuildError`, `save_capture`, `load_snapshot`, `SnapshotError` (8) and `LayoutApp` (10)
- Produces: the typer `app`, with the commands `capture [-s SERIAL] [--adb PATH] [-o DIR]` and `inspect [SNAPSHOT_DIR] [-s] [--adb]`. It also provides the module-level `_make_adb(adb_path, serial) -> Adb` and the `LayoutApp` name, both patched in tests. The default output dir is `layout-snapshots/<package>-<YYYYmmdd-HHMMSS>`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from helpers import FakeAdb, views_raw, views_responses, views_snapshot
from typer.testing import CliRunner

from layoutcli import cli
from layoutcli.adb import AdbError
from layoutcli.snapshot_io import save_capture

runner = CliRunner()


def test_capture_writes_snapshot_and_prints_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    out = tmp_path / "snap"
    result = runner.invoke(cli.app, ["capture", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "snapshot.json").is_file()
    assert "18 views" in result.output
    assert "com.example.demo.MainActivity" in result.output


def test_capture_reports_adb_error(monkeypatch):
    def boom(adb, serial):
        raise AdbError("no Android device connected")
    monkeypatch.setattr(cli, "_make_adb", boom)
    result = runner.invoke(cli.app, ["capture"])
    assert result.exit_code == 1
    assert "no Android device connected" in result.output


def test_inspect_opens_saved_snapshot(tmp_path, monkeypatch):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    opened = []

    class FakeApp:
        def __init__(self, snapshot):
            opened.append(snapshot)

        def run(self):
            pass

    monkeypatch.setattr(cli, "LayoutApp", FakeApp)
    result = runner.invoke(cli.app, ["inspect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert opened[0].package == "com.example.demo"


def test_inspect_without_dir_captures_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    opened = []
    monkeypatch.setattr(cli, "LayoutApp", lambda snap: type("A", (), {"run": lambda self: opened.append(snap)})())
    result = runner.invoke(cli.app, ["inspect"])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1
    assert list((tmp_path / "layout-snapshots").iterdir())


def test_inspect_rejects_non_snapshot_dir(tmp_path):
    result = runner.invoke(cli.app, ["inspect", str(tmp_path)])
    assert result.exit_code == 1
    assert "not a layoutcli snapshot" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'cli'`

- [ ] **Step 3: Implement `src/layoutcli/cli.py`**

```python
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from typing_extensions import Annotated

from layoutcli.adb import Adb, AdbError, find_adb
from layoutcli.build import BuildError, build_snapshot
from layoutcli.capture import capture_raw
from layoutcli.model import Snapshot
from layoutcli.snapshot_io import SnapshotError, load_snapshot, save_capture
from layoutcli.tui.app import LayoutApp

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="Capture and inspect Android app layouts.")
console = Console(soft_wrap=True)
err_console = Console(stderr=True, soft_wrap=True)

SerialOpt = Annotated[Optional[str], typer.Option("--serial", "-s", help="Device serial (see `adb devices`).")]
AdbOpt = Annotated[Optional[str], typer.Option("--adb", help="Path to adb or its directory.")]


def _make_adb(adb_path: str | None, serial: str | None) -> Adb:
    return Adb.connect(find_adb(adb_path), serial)


def _default_out(snap: Snapshot) -> Path:
    name = re.sub(r"[^\w.-]", "_", snap.package or "unknown")
    return Path("layout-snapshots") / f"{name}-{datetime.now():%Y%m%d-%H%M%S}"


def _print_summary(snap: Snapshot, out_dir: Path) -> None:
    count = sum(1 for _ in snap.root.walk())
    console.print(f"Captured {count} views from {escape(snap.activity or snap.package or 'unknown app')}")
    for source, status in snap.capabilities.items():
        style = "green" if status == "ok" else "yellow"
        console.print(f"  {source:<12} [{style}]{escape(status)}[/]")
    console.print(f"Saved to {escape(str(out_dir))}")


def _capture(adb_path: str | None, serial: str | None, out: Path | None) -> Path:
    adb = _make_adb(adb_path, serial)
    raw = capture_raw(adb)
    snap = build_snapshot(raw, captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    out_dir = out or _default_out(snap)
    save_capture(raw, snap, out_dir)
    _print_summary(snap, out_dir)
    return out_dir


def _fail(error: Exception) -> typer.Exit:
    err_console.print(f"[red]error:[/] {escape(str(error))}")
    return typer.Exit(code=1)


@app.command()
def capture(serial: SerialOpt = None, adb: AdbOpt = None,
            out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Snapshot directory.")] = None
            ) -> None:
    """Capture the foreground screen's layout into a snapshot directory."""
    try:
        _capture(adb, serial, out)
    except (AdbError, BuildError) as e:
        raise _fail(e)


@app.command()
def inspect(snapshot_dir: Annotated[Optional[Path], typer.Argument(
                help="Snapshot directory; captures a new one when omitted.")] = None,
            serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """Open a snapshot in the interactive inspector."""
    try:
        directory = snapshot_dir if snapshot_dir is not None else _capture(adb, serial, None)
        snap = load_snapshot(directory)
    except (AdbError, BuildError, SnapshotError) as e:
        raise _fail(e)
    LayoutApp(snap).run()
```

`typing_extensions` is installed as a dependency of typer. On 3.10+ `typing.Annotated` also works; use whichever import style the linter prefers, and keep it consistent.

`README.md`:
````markdown
# layoutcli

Inspect the layout of a running Android app from the terminal: one-shot capture, then explore it offline.

## Install

    uv sync
    uv run layoutcli --help

## Usage

    layoutcli capture [-s SERIAL] [--adb PATH] [-o DIR]   # save snapshot of the foreground screen
    layoutcli inspect [DIR]                               # open TUI (captures first if DIR omitted)

adb lookup order: `--adb`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `sdk.dir` in `local.properties`
(searched from the current directory upward), `PATH`, default SDK locations.

Data sources: `dumpsys activity top` (real View tree, including GONE views), `uiautomator dump`
(semantics incl. Compose), `screencap`. Missing sources are reported, not fatal.
In the tree, `◇` marks nodes known only from uiautomator (e.g. Compose semantics).

TUI keys: arrows to navigate the tree, `q` to quit.
````

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -v`
Expected: the whole suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/layoutcli/cli.py README.md tests/test_cli.py
git commit -m "feat: capture and inspect CLI commands" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```

---

### Task 12: End-to-end check on a real device

**Files:**
- Modify (only if bugs are found): the affected module plus a regression test that uses the real dump saved as a fixture

- [ ] **Step 1: Connect a device or emulator and run the capture**

Run: `uv run layoutcli capture -o layout-snapshots/e2e`
Expected: `Captured N views from <your activity>`, with `dumpsys`, `uiautomator` and `screenshot` all `ok`. adb is found even though it is not on PATH, through `ANDROID_HOME`/`local.properties` or the `%LOCALAPPDATA%\Android\Sdk` fallback.

- [ ] **Step 2: Inspect the snapshot**

Run: `uv run layoutcli inspect layout-snapshots/e2e`
Expected:
- The tree matches Android Studio's Layout Inspector for a Views screen.
- Compose content appears under `AndroidComposeView` with `◇` markers.
- Selecting a node highlights a box in the wireframe at the right place.
- `q` exits.

- [ ] **Step 3: Check degradation**

- Run while an infinite animation is on screen. The capture should still succeed, with uiautomator reporting "could not get idle state".
- Run with two devices attached and no `-s`. Expect a clear error that lists both serials.

- [ ] **Step 4: Turn surprises into fixtures**

If a real `raw/dumpsys.txt` or `raw/uiautomator.xml` breaks parsing or matching, copy it to `tests/fixtures/`, write a failing test, fix the code, then run `uv run pytest`.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A src tests
git commit -m "fix: <what the real device revealed>" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013XMDa2pcWQ5Aj7ikBMxrqM"
```
