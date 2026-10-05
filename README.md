# layoutcli

Inspect the layout of a running Android app from the terminal: one-shot capture, then explore it offline.

## Install

    uv sync
    uv run layoutcli --help

## Usage

    layoutcli [-s SERIAL] [--adb PATH]                    # capture, then open the TUI on the new snapshot
    layoutcli capture [-s SERIAL] [--adb PATH] [-o DIR]   # save snapshot of the foreground screen
    layoutcli inspect [DIR]                               # open TUI; without DIR pick a saved snapshot (or n = new)

Snapshots go to `layout-snapshots/capture-YYYYMMDD-HHMMSS` unless `-o` is given.

adb lookup order: `--adb`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `sdk.dir` in `local.properties`
(searched from the current directory upward), `PATH`, default SDK locations.

Data sources: `dumpsys activity top` (real View tree, including GONE views), `uiautomator dump`
(semantics incl. Compose), `screencap`. Missing sources are reported, not fatal.
In the tree, `◇` marks nodes known only from uiautomator (e.g. Compose semantics).

TUI keys: arrows to navigate the tree, `q` to quit.
