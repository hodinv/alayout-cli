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

Snapshots go to `layout-snapshots/capture-YYYYMMDD-HHMMSS` unless `-o` is given.

adb lookup order: `--adb`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `sdk.dir` in `local.properties`
(searched from the current directory upward), `PATH`, default SDK locations.

Data sources: `dumpsys activity top` (real View tree, including GONE views), `uiautomator dump`
(semantics incl. Compose), `screencap`. Missing sources are reported, not fatal.
In the tree, `◇` marks nodes known only from uiautomator (e.g. Compose semantics).

TUI keys: arrows navigate the tree, `/` search (id, class, text, content-desc), `n`/`N` next/previous match,
`f` filter the tree to matches, `p` switch wireframe/screenshot, `c` checks list (Enter jumps to the view), `q` quit.
Views with warnings are marked `⚠`.
