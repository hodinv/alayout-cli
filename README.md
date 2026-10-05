# layoutcli

Inspect the layout of a running Android app from the terminal: one-shot capture, then explore it offline.

## Install

    uv sync
    uv run layoutcli --help

## Usage

    layoutcli [-s SERIAL] [--adb PATH]                    # capture, then open the TUI on the new snapshot
    layoutcli capture [-s SERIAL] [--adb PATH] [-o DIR]   # save snapshot of the foreground screen
    layoutcli inspect [DIR]                               # open TUI; without DIR pick a saved snapshot (or n = new)
    layoutcli check [DIR]                                 # report layout problems (touch targets, labels, overlaps...)
    layoutcli diff [A] [B]                                # views added/removed/changed between two snapshots
    layoutcli composables [--apk PATH] [--all] [--previews] # the app's composable functions (debug APK)

`--apk PATH` or `--apk device` (on `layoutcli`, `capture`, `inspect`) maps view ids to the layout XML files
that declare them; needs Android SDK build-tools (aapt2). The APK itself is not stored, only `apk.json`. Options may also go before the command (`layoutcli --apk device capture`); for an existing snapshot `--apk` is ignored.

Snapshots go to `layout-snapshots/capture-YYYYMMDD-HHMMSS` unless `-o` is given.

adb lookup order: `--adb`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `sdk.dir` in `local.properties`
(searched from the current directory upward), `PATH`, default SDK locations.

Data sources: `dumpsys activity top` (real View tree, including GONE views), `uiautomator dump`
(semantics incl. Compose), `screencap`. Missing sources are reported, not fatal.
In the tree, `◇` marks nodes known only from uiautomator (e.g. Compose semantics).
Inside Compose, nodes are tagged with the component they most likely are, inferred from semantics:
`⟨Button "OK"⟩`, `⟨Clickable "Row text"⟩`, `⟨IconButton "Back"⟩`, `⟨Selector⟩`, `⟨Toggle … [checked]⟩`,
`⟨TextField⟩`, `⟨Scrollable⟩`, with `(n of m similar)` for siblings of identical structure (likely the same
composable). Real composable names are not available from semantics; `layoutcli composables` lists the app's
composables (name, file, approximate line) from the Compose source information kept in debug APKs, as a reference.

TUI keys: arrows navigate the tree, `/` search (id, class, text, content-desc), `n`/`N` next/previous match,
`f` filter the tree to matches, `p` switch wireframe/screenshot, `s` full-screen screenshot, `o` open the real PNG in the system image viewer (selected view outlined, the rest dimmed), `c` checks list (Enter jumps to the view), `x` layout XML of the view (with `--apk`), `q` quit.
Views with warnings are marked `⚠`.
