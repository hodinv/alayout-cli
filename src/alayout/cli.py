from __future__ import annotations

import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.markup import escape

from alayout import __version__
from alayout.adb import Adb, AdbError, find_adb
from alayout.apk import ApkError, apply_index, build_index, find_aapt2
from alayout.build import BuildError, build_snapshot
from alayout.capture import capture_raw, pull_apks
from alayout.checks import run_checks
from alayout.composables import app_composables, list_composables
from alayout.compose import compose_nodes, infer_components
from alayout.diff import diff_snapshots
from alayout.format import node_label
from alayout.model import Snapshot
from alayout.parse.dumpsys import parse_dumpsys
from alayout.snapshot_io import SnapshotError, list_snapshots, load_snapshot, save_apk_index, save_capture
from alayout.tui.app import LayoutApp

app = typer.Typer(add_completion=False,
                  help="Capture and inspect Android app layouts. "
                       "Without a command: capture, then open the inspector.")
console = Console(soft_wrap=True, highlight=False)
err_console = Console(stderr=True, soft_wrap=True)

SerialOpt = Annotated[Optional[str], typer.Option("--serial", "-s", help="Device serial (see `adb devices`).")]
AdbOpt = Annotated[Optional[str], typer.Option("--adb", help="Path to adb or its directory.")]
ApkOpt = Annotated[Optional[str], typer.Option(
    "--apk", help="Map view ids to layout XML: the app's APK, a folder of APKs (base + splits), "
                  "or 'device' to pull them (needs SDK build-tools).")]


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


def _choose_snapshot(title: str = "Snapshot") -> Path | None:
    """Let the user pick a saved snapshot; None means capture a new one."""
    snapshots = list_snapshots(SNAPSHOTS_DIR)
    if not snapshots:
        return None
    console.print(f"[bold]{escape(title)}[/]")
    for number, (folder, snap) in enumerate(snapshots, start=1):
        console.print(f"  [bold]{number:>2}[/]  {escape(folder.name):<28} "
                      f"{escape(snap.activity or snap.package or '?')}  [dim]{escape(snap.captured_at)}[/]")
    console.print("   [bold]n[/]  new capture")
    while True:
        answer = typer.prompt(title, default="1").strip().lower()
        if answer == "n":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(snapshots):
            return snapshots[int(answer) - 1][0]
        err_console.print(f"choose 1-{len(snapshots)} or n")


def _resolve_snapshot(snapshot_dir: Path | None, adb: str | None, serial: str | None,
                      title: str = "Snapshot", apk: str | None = None) -> Path:
    directory = snapshot_dir if snapshot_dir is not None else _choose_snapshot(title)
    if directory is None:
        return _capture(adb, serial, None, apk)
    if apk:
        err_console.print("[yellow]warning:[/] --apk is ignored for an existing snapshot "
                          "(it applies to new captures)")
    return directory


def _print_summary(snap: Snapshot, out_dir: Path) -> None:
    count = sum(1 for _ in snap.root.walk())
    console.print(f"Captured {count} views from {escape(snap.activity or snap.package or 'unknown app')}")
    for source, status in snap.capabilities.items():
        style = "green" if status == "ok" else "yellow"
        console.print(f"  {source:<12} [{style}]{escape(status)}[/]")
    components = len(infer_components(snap.root))
    if components:
        console.print(f"Compose UI: {components} component{'' if components == 1 else 's'} inferred from semantics")
    console.print(f"Saved to {escape(str(out_dir))}")


def _local_apks(apk: str) -> list[Path]:
    """--apk PATH: one APK, or a folder with base.apk and split APKs."""
    path = Path(apk)
    if not path.is_dir():
        return [path]
    found = sorted(path.glob("*.apk"), key=lambda p: (p.name != "base.apk", p.name))
    if not found:
        raise ApkError(f"no .apk files in {path}")
    return found


def _apk_index(adb: Adb, apk: str, package: str | None):
    aapt2 = find_aapt2(getattr(adb, "adb_path", None))
    if apk != "device":
        return build_index(_local_apks(apk), aapt2)
    if not package:
        raise ApkError("cannot pull the APK: package unknown")
    with tempfile.TemporaryDirectory() as tmp:
        return build_index(pull_apks(adb, package, Path(tmp)), aapt2)


def _capture(adb_path: str | None, serial: str | None, out: Path | None, apk: str | None = None) -> Path:
    adb = _make_adb(adb_path, serial)
    raw = capture_raw(adb)
    snap = build_snapshot(raw, captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    index = None
    if apk:
        try:
            index = _apk_index(adb, apk, snap.package)
            snap.capabilities["apk"] = f"ok ({apply_index(snap.root, index)} views mapped)"
        except (ApkError, AdbError, OSError) as e:
            index = None
            snap.capabilities["apk"] = str(e)
    out_dir = out or _default_out()
    save_capture(raw, snap, out_dir)
    if index is not None:
        save_apk_index(index.restricted_to({n.id for n, _ in snap.root.walk() if "apk" in n.props}), out_dir)
    _print_summary(snap, out_dir)
    return out_dir


def _global(ctx: typer.Context, serial: str | None, adb: str | None, apk: str | None):
    """Options given before the command (alayout --apk device capture) fill in unset ones."""
    given = ctx.obj or {}
    return serial or given.get("serial"), adb or given.get("adb"), apk or given.get("apk")


def _self_test(directory: Path) -> int:
    """Open a snapshot in the full TUI headless and print a summary.

    Verifies an installed package or a frozen binary (all modules and widgets load, the
    screenshot renders). Exit code 0 on success.
    """
    try:
        snap = load_snapshot(directory)
    except SnapshotError as e:
        console.print(f"alayout {__version__} self-test: FAILED {escape(str(e))}")
        return 1

    async def pilot_script(pilot) -> None:
        app = pilot.app
        await pilot.pause()
        problems = []
        if app._image is not None:
            await pilot.press("p")
            await pilot.pause()
            if "\u2580" not in app.query_one("#wire").render().plain:
                problems.append("screenshot preview not rendered")
            await pilot.press("s")
            await pilot.pause()
            if app.screen.__class__.__name__ != "ScreenshotScreen":
                problems.append("full-screen screenshot not shown")
            await pilot.press("escape")
        await pilot.press("c")
        await pilot.pause()
        views = sum(1 for _ in snap.root.walk())
        warnings = sum(1 for i in app.issues if i.severity == "warning")
        shot = "{}x{}".format(*app._image.size) if app._image is not None else "none"
        status = "FAILED " + "; ".join(problems) if problems else "OK"
        app.exit(f"{status} {views} views, {warnings} warnings, screenshot {shot}")

    result = LayoutApp(snap, base_dir=directory).run(headless=True, auto_pilot=pilot_script)
    console.print(f"alayout {__version__} self-test: {escape(str(result))}")
    return 0 if isinstance(result, str) and result.startswith("OK") else 1


def _fail(error: Exception) -> typer.Exit:
    err_console.print(f"[red]error:[/] {escape(str(error))}")
    return typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, serial: SerialOpt = None, adb: AdbOpt = None, apk: ApkOpt = None,
         version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
         self_test: Annotated[Optional[Path], typer.Option(
             "--self-test", metavar="SNAPSHOT_DIR",
             help="Open a snapshot headless, print a summary and exit (checks an installation).")] = None,
         ) -> None:
    """Capture and inspect Android app layouts."""
    _utf8_output()
    if version:
        console.print(f"alayout {__version__}")
        raise typer.Exit()
    if self_test is not None:
        raise typer.Exit(code=_self_test(self_test))
    ctx.obj = {"serial": serial, "adb": adb, "apk": apk}  # also accepted before the command
    if ctx.invoked_subcommand is None:
        try:
            directory = _capture(adb, serial, None, apk)
            snap = load_snapshot(directory)
        except (AdbError, BuildError, SnapshotError, OSError) as e:
            raise _fail(e)
        LayoutApp(snap, base_dir=directory).run()


@app.command()
def capture(ctx: typer.Context, serial: SerialOpt = None, adb: AdbOpt = None, apk: ApkOpt = None,
            out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Snapshot directory.")] = None
            ) -> None:
    """Capture the foreground screen's layout into a snapshot directory."""
    serial, adb, apk = _global(ctx, serial, adb, apk)
    try:
        _capture(adb, serial, out, apk)
    except (AdbError, BuildError, OSError) as e:
        raise _fail(e)


@app.command()
def inspect(ctx: typer.Context, snapshot_dir: Annotated[Optional[Path], typer.Argument(
                help="Snapshot directory; when omitted, pick a saved one or capture a new one.")] = None,
            serial: SerialOpt = None, adb: AdbOpt = None, apk: ApkOpt = None) -> None:
    """Open a snapshot in the interactive inspector."""
    serial, adb, apk = _global(ctx, serial, adb, apk)
    try:
        directory = _resolve_snapshot(snapshot_dir, adb, serial, apk=apk)
        snap = load_snapshot(directory)
    except (AdbError, BuildError, SnapshotError, OSError) as e:
        raise _fail(e)
    LayoutApp(snap, base_dir=directory).run()


@app.command()
def check(ctx: typer.Context, snapshot_dir: Annotated[Optional[Path], typer.Argument(
              help="Snapshot directory; when omitted, pick a saved one or capture a new one.")] = None,
          serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """Report layout problems: small touch targets, missing labels, overlaps, deep nesting..."""
    serial, adb, _ = _global(ctx, serial, adb, None)
    try:
        snap = load_snapshot(_resolve_snapshot(snapshot_dir, adb, serial))
    except (AdbError, BuildError, SnapshotError) as e:
        raise _fail(e)
    issues = run_checks(snap)
    components = infer_components(snap.root)
    in_compose = compose_nodes(snap.root)
    warnings = sum(1 for i in issues if i.severity == "warning")
    console.print(f"{warnings} warning{'' if warnings == 1 else 's'}, {len(issues) - warnings} info in "
                  f"{escape(snap.activity or snap.package or 'unknown app')}")
    for issue in sorted(issues, key=lambda i: (i.severity != "warning", i.check)):
        style = "yellow" if issue.severity == "warning" else "dim"
        console.print(f"  [{style}]{issue.severity:<7}[/] {issue.check:<22} "
                      f"{escape(node_label(issue.node, warning=issue.severity == 'warning', component=components.get(issue.node), in_compose=issue.node in in_compose).plain)}  "
                      f"{escape(issue.message)}")


def _sources(snap: Snapshot) -> set[str]:
    return {source for node, _ in snap.root.walk() for source in node.sources}


def _quote(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


@app.command()
def diff(ctx: typer.Context, first: Annotated[Optional[Path], typer.Argument(help="Older snapshot (picked when omitted).")] = None,
         second: Annotated[Optional[Path], typer.Argument(help="Newer snapshot (picked when omitted).")] = None,
         serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """Show views added, removed and changed between two snapshots."""
    serial, adb, _ = _global(ctx, serial, adb, None)
    try:
        dir_a = _resolve_snapshot(first, adb, serial, "First snapshot")
        dir_b = _resolve_snapshot(second, adb, serial, "Second snapshot")
        snap_a, snap_b = load_snapshot(dir_a), load_snapshot(dir_b)
    except (AdbError, BuildError, SnapshotError) as e:
        raise _fail(e)
    console.print(f"{escape(dir_a.name)} ({escape(snap_a.activity or '?')}) -> "
                  f"{escape(dir_b.name)} ({escape(snap_b.activity or '?')})")
    same_sources = _sources(snap_a) == _sources(snap_b)
    if not same_sources:
        console.print("[yellow]warning:[/] the snapshots come from different sources "
                      f"({', '.join(sorted(_sources(snap_a))) or '-'} vs {', '.join(sorted(_sources(snap_b))) or '-'}); "
                      "class names are not compared and views without ids may not line up")
    result = diff_snapshots(snap_a.root, snap_b.root, compare_class=same_sources)
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


@app.command()
def composables(apk: ApkOpt = None,
                show_all: Annotated[bool, typer.Option("--all", help="Include library composables.")] = False,
                previews: Annotated[bool, typer.Option("--previews", help="Include @Preview functions.")] = False,
                serial: SerialOpt = None, adb: AdbOpt = None) -> None:
    """List the app's composable functions found in its (debug) APK. Without --apk: the foreground app."""
    package = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            if apk and apk != "device":
                apk_paths = _local_apks(apk)
            else:
                device = _make_adb(adb, serial)
                top = parse_dumpsys(device.exec_out("dumpsys activity top").decode("utf-8", "replace"))
                if top is None or not top.package:
                    raise AdbError("no foreground app found; open the app or pass --apk PATH")
                package = top.package
                apk_paths = pull_apks(device, package, Path(tmp))
            items = list_composables(apk_paths)
    except (AdbError, ApkError, OSError, zipfile.BadZipFile) as e:
        raise _fail(e)
    shown = items if show_all else app_composables(items, package)
    if not previews:
        shown = [c for c in shown if not c.preview]
    if not shown:
        console.print("no composables found (release/minified builds drop Compose source information)")
        return
    files: dict[tuple[str, str], list] = {}
    for c in shown:
        files.setdefault((c.package or "?", c.file), []).append(c)
    console.print(f"{len(shown)} composables in {len(files)} files"
                  + (f" ({escape(package)})" if package else "")
                  + ("" if previews else ", previews hidden (--previews)"))
    for (pkg, file), group in files.items():
        console.print(f"  [bold]{escape(file)}[/]  [dim]{escape(pkg)}[/]")
        for c in group:
            where = f"~{c.line}" if c.line else ""
            console.print(f"    {escape(c.name):<40} [dim]{where}[/]")
