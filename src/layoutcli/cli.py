from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape

from layoutcli.adb import Adb, AdbError, find_adb
from layoutcli.build import BuildError, build_snapshot
from layoutcli.capture import capture_raw
from layoutcli.model import Snapshot
from layoutcli.snapshot_io import SnapshotError, load_snapshot, save_capture
from layoutcli.tui.app import LayoutApp

app = typer.Typer(add_completion=False,
                  help="Capture and inspect Android app layouts. "
                       "Without a command: capture, then open the inspector.")
console = Console(soft_wrap=True)
err_console = Console(stderr=True, soft_wrap=True)

SerialOpt = Annotated[Optional[str], typer.Option("--serial", "-s", help="Device serial (see `adb devices`).")]
AdbOpt = Annotated[Optional[str], typer.Option("--adb", help="Path to adb or its directory.")]


def _make_adb(adb_path: str | None, serial: str | None) -> Adb:
    return Adb.connect(find_adb(adb_path), serial)


def _default_out() -> Path:
    return Path("layout-snapshots") / f"capture-{datetime.now():%Y%m%d-%H%M%S}"


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
    if ctx.invoked_subcommand is None:
        inspect(None, serial=serial, adb=adb)


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
