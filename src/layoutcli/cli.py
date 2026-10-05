from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape

from layoutcli.adb import Adb, AdbError, find_adb
from layoutcli.build import BuildError, build_snapshot
from layoutcli.capture import capture_raw
from layoutcli.checks import run_checks
from layoutcli.format import node_label
from layoutcli.model import Snapshot
from layoutcli.snapshot_io import SnapshotError, list_snapshots, load_snapshot, save_capture
from layoutcli.tui.app import LayoutApp

app = typer.Typer(add_completion=False,
                  help="Capture and inspect Android app layouts. "
                       "Without a command: capture, then open the inspector.")
console = Console(soft_wrap=True, highlight=False)
err_console = Console(stderr=True, soft_wrap=True)

SerialOpt = Annotated[Optional[str], typer.Option("--serial", "-s", help="Device serial (see `adb devices`).")]
AdbOpt = Annotated[Optional[str], typer.Option("--adb", help="Path to adb or its directory.")]


def _utf8_output() -> None:
    """Redirected output on Windows defaults to cp1252, which cannot encode ◇/⚠ labels."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _make_adb(adb_path: str | None, serial: str | None) -> Adb:
    return Adb.connect(find_adb(adb_path), serial)


SNAPSHOTS_DIR = Path("layout-snapshots")


def _default_out() -> Path:
    return SNAPSHOTS_DIR / f"capture-{datetime.now():%Y%m%d-%H%M%S}"


def _choose_snapshot() -> Path | None:
    """Let the user pick a saved snapshot; None means capture a new one."""
    snapshots = list_snapshots(SNAPSHOTS_DIR)
    if not snapshots:
        return None
    for number, (folder, snap) in enumerate(snapshots, start=1):
        console.print(f"  [bold]{number:>2}[/]  {escape(folder.name):<28} "
                      f"{escape(snap.activity or snap.package or '?')}  [dim]{escape(snap.captured_at)}[/]")
    console.print("   [bold]n[/]  new capture")
    while True:
        answer = typer.prompt("Snapshot", default="1").strip().lower()
        if answer == "n":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(snapshots):
            return snapshots[int(answer) - 1][0]
        err_console.print(f"choose 1-{len(snapshots)} or n")


def _resolve_snapshot(snapshot_dir: Path | None, adb: str | None, serial: str | None) -> Path:
    directory = snapshot_dir if snapshot_dir is not None else _choose_snapshot()
    return directory if directory is not None else _capture(adb, serial, None)


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
    out_dir = out or _default_out()
    save_capture(raw, snap, out_dir)
    _print_summary(snap, out_dir)
    return out_dir


def _fail(error: Exception) -> typer.Exit:
    err_console.print(f"[red]error:[/] {escape(str(error))}")
    return typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """Capture and inspect Android app layouts."""
    _utf8_output()
    if ctx.invoked_subcommand is None:
        try:
            snap = load_snapshot(_capture(adb, serial, None))
        except (AdbError, BuildError, SnapshotError) as e:
            raise _fail(e)
        LayoutApp(snap).run()


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
                help="Snapshot directory; when omitted, pick a saved one or capture a new one.")] = None,
            serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """Open a snapshot in the interactive inspector."""
    try:
        directory = _resolve_snapshot(snapshot_dir, adb, serial)
        snap = load_snapshot(directory)
    except (AdbError, BuildError, SnapshotError) as e:
        raise _fail(e)
    LayoutApp(snap).run()


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
                      f"{escape(node_label(issue.node, warning=issue.severity == 'warning').plain)}  "
                      f"{escape(issue.message)}")
