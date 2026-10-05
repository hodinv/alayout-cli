# alayout

Inspect the layout of a running Android app from the terminal: one-shot capture, then explore it offline.

## Install

The PyPI package is `alayout-cli`; the command it installs is `alayout`.

    uv tool install alayout-cli      # or: pipx install alayout-cli  (once published)
    uv tool install .                # from a checkout of this repository

For development:

    uv sync
    uv run alayout --help

## Usage

    alayout [-s SERIAL] [--adb PATH]                    # capture, then open the TUI on the new snapshot
    alayout capture [-s SERIAL] [--adb PATH] [-o DIR]   # save snapshot of the foreground screen
    alayout inspect [DIR]                               # open TUI; without DIR pick a saved snapshot (or n = new)
    alayout check [DIR]                                 # report layout problems (touch targets, labels, overlaps...)
    alayout diff [A] [B]                                # views added/removed/changed between two snapshots
    alayout composables [--apk PATH] [--all] [--previews] # the app's composable functions (debug APK)

`--apk PATH` (an APK, or a folder with `base.apk` and split APKs) or `--apk device` (pulls base and splits) (on `alayout`, `capture`, `inspect`) maps view ids to the layout XML files
that declare them; needs Android SDK build-tools (aapt2). The APK itself is not stored, only `apk.json`. Options may also go before the command (`alayout --apk device capture`); for an existing snapshot `--apk` is ignored.
aapt2 is looked up next to adb, in `ANDROID_HOME`/`ANDROID_SDK_ROOT`, the project's `local.properties` and the default SDK folder (Windows `%LOCALAPPDATA%/Android/Sdk`, macOS `~/Library/Android/sdk`, Linux `~/Android/Sdk`).

Snapshots go to `layout-snapshots/capture-YYYYMMDD-HHMMSS` unless `-o` is given.

adb lookup order: `--adb`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`, `sdk.dir` in `local.properties`
(searched from the current directory upward), `PATH`, default SDK locations.

Data sources: `dumpsys activity top` (real View tree, including GONE views), `uiautomator dump`
(semantics incl. Compose), `screencap`. Missing sources are reported, not fatal.
In the tree, `◇` marks nodes known only from uiautomator (e.g. Compose semantics).
Inside Compose, nodes are tagged with the component they most likely are, inferred from semantics:
`⟨Button "OK"⟩`, `⟨Clickable "Row text"⟩`, `⟨IconButton "Back"⟩`, `⟨Selector⟩`, `⟨Toggle … [checked]⟩`,
`⟨TextField⟩`, `⟨Scrollable⟩`, with `(n of m similar)` for siblings of identical structure (likely the same
composable). Real composable names are not available from semantics; `alayout composables` lists the app's
composables (name, file, approximate line) from the Compose source information kept in debug APKs, as a reference.

TUI keys: arrows navigate the tree, `/` search (id, class, text, content-desc), `n`/`N` next/previous match,
`f` filter the tree to matches, `p` switch wireframe/screenshot, `s` full-screen screenshot, `o` open the real PNG in the system image viewer (selected view outlined, the rest dimmed), `c` checks list (Enter jumps to the view), `x` layout XML of the view (with `--apk`), `q` quit.
Views with warnings are marked `⚠`.

## Platforms

Windows, macOS and Linux. The screenshot preview needs a 24-bit colour terminal (Windows Terminal, iTerm2,
WezTerm, kitty, Ghostty, GNOME Terminal...); macOS Terminal.app only has 256 colours, so use `o` there to open
the real PNG. `o` uses the default image viewer (`open` on macOS, `xdg-open` on Linux); without a desktop
session (e.g. over SSH) it reports where the annotated PNG was saved instead.
