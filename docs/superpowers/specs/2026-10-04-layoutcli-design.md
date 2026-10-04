# LayoutCli: Android layout debugging CLI (Python)

## Context
An empty repo (`c:\Work\LayoutCli`) for a new tool. The goal is to debug the UI layout of a debuggable Android app (classic XML Views and/or Compose) from the terminal. It takes one snapshot from the device (no realtime), then lets you explore the hierarchy, properties and a visual preview in an interactive TUI. It finds adb on PATH or through `sdk.dir` in `local.properties`. It uses whatever data it can get and degrades gracefully: the APK may or may not be available, and Compose gives less data than Views.

Agreed with the user:
- Target: debuggable apps, mixed Views and Compose, with an optional APK.
- UI: an interactive TUI (`textual`).
- Preview: a box-drawing wireframe and a half-block truecolor screenshot, switched with a key. The selected view is highlighted in both.
- v1 extras: snapshot save/load and diff, search/filter in the TUI, layout checks.
- Not in v1: HTML export, realtime.

## Data sources (layered capture)
| Tier | Source | Gives |
|---|---|---|
| Always | `uiautomator dump` | Semantics tree (also covers Compose): class, resource-id, text, content-desc, absolute bounds, flags |
| Always | `dumpsys activity top` | Real View tree: GONE/INVISIBLE views, view flags, relative bounds, ids, view hash |
| Always | `screencap -p` (exec-out) | PNG for the screenshot preview |
| Always | `wm density` / `wm size` | Converting px to dp for display and checks |
| APK (local `--apk`, or pulled via `pm path` + `adb pull`) | `androguard` resource decoding | Map ids to layout XML file names, resolve dimen and style names, show the original layout XML |
| Debuggable, phase 2 | JDWP + DDM `VURT`/`VULW` (ViewDebug dump) | Every `@ExportedProperty`: padding, margins, LayoutParams, alpha and so on |

Merging: the dumpsys View tree is the backbone. uiautomator nodes are attached to it by matching bounds, id and class, which adds text and semantics. The Compose subtree under `AndroidComposeView` comes from uiautomator only. Each property records which source it came from.

## Architecture (`src/layoutcli/`)
- `adb.py`: finds adb (in order: `--adb` flag, `ANDROID_HOME`/`ANDROID_SDK_ROOT`, `local.properties` `sdk.dir` searched upward from the cwd, PATH), picks the device (`-s`), runs commands.
- `capture/`: one module per source (`uiautomator.py`, `dumpsys.py`, `screen.py`, `apk.py`, later `jdwp.py`). Each returns raw data or `None` with a reason.
- `model.py`: `ViewNode` (class, id, bounds abs/rel, visibility, text, props dict keyed by source, children) and `Snapshot` (metadata, root, screenshot path, density, capabilities).
- `merge.py`: builds the unified tree from the raw sources.
- `snapshot_io.py`: save and load a snapshot folder (`snapshot.json`, `screen.png`, raw dumps kept for re-parsing).
- `checks.py`: layout checks (nesting depth > N, touch target < 48dp, overlapping clickables, views off-screen or zero-size, missing content-desc on image/clickable views, invisible views that still take space).
- `diff.py`: matches nodes between two snapshots (by id path, then class and bounds) and reports added, removed and changed nodes and properties.
- `tui/`: a `textual` app. Panels: the tree (with check badges), a properties table, and a preview (`wireframe.py` scaled box drawing, `screenshot.py` half-block rendering via Pillow). Keys: `/` to search, `f` to filter, `p` to switch the preview, `c` for the checks list.
- `cli.py` (`typer`) commands:
  - `layoutcli capture [-s serial] [--apk path] [-o dir]`
  - `layoutcli inspect [snapshot_dir]` (captures first if no folder is given)
  - `layoutcli diff a b`
  - `layoutcli check snapshot`

Dependencies: Python 3.10+, `textual`, `rich`, `typer`, `Pillow`, `androguard` (optional extra `[apk]`). Packaged with `pyproject.toml` and the console script `layoutcli`.

## Phasing
1. adb discovery, uiautomator and dumpsys capture, model and merge, snapshot I/O, and a TUI with tree, properties and the wireframe.
2. Screenshot preview and highlight, search/filter, checks.
3. Snapshot diff and APK decoding.
4. JDWP ViewDebug deep properties for Views. This is riskier: the DDM protocol has to be implemented in Python. Compose deep properties (Studio's inspector agent) stay out of scope.

## Next steps after approval
1. Write the spec to `docs/superpowers/specs/2026-10-04-layoutcli-design.md` (and `git init`), then have the user review it.
2. Write the implementation plan (writing-plans skill), then implement with TDD.

## Verification
- Unit tests: parsers run against recorded fixture dumps (uiautomator XML, dumpsys text from a Views app and a Compose app), merge, checks, diff, and adb path resolution including a fake `local.properties`.
- TUI: the `textual` pilot snapshot tests on a fixture snapshot.
- End to end: run `layoutcli capture` against a device or emulator with a sample app, then `layoutcli inspect` on it. Confirm the tree matches Layout Inspector, the highlight lines up on the screenshot, and the app still works with the APK unavailable.
