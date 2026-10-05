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


def list_snapshots(base: Path) -> list[tuple[Path, Snapshot]]:
    """Readable snapshot folders directly under `base`, newest capture first."""
    if not base.is_dir():
        return []
    found = []
    for folder in base.iterdir():
        if (folder / SNAPSHOT_FILE).is_file():
            try:
                found.append((folder, load_snapshot(folder)))
            except SnapshotError:
                continue
    return sorted(found, key=lambda item: (item[1].captured_at, item[0].name), reverse=True)
