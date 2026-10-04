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


def _window_xml(package, bounds, rotation=0):
    return ("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
            f'<hierarchy rotation="{rotation}"><node index="0" text="" resource-id="" class="android.widget.FrameLayout" '
            f'package="{package}" content-desc="" bounds="{bounds}">'
            '<node index="0" text="Allow" resource-id="" class="android.widget.Button" '
            f'package="{package}" content-desc="" bounds="[100,1000][500,1100]" /></node></hierarchy>')


def test_foreign_active_window_is_not_merged():
    raw = views_raw()
    raw.uiautomator_xml = _window_xml("com.android.permissioncontroller", "[0,0][1080,2400]")
    snap = build_snapshot(raw, AT)
    assert "com.android.permissioncontroller" in snap.capabilities["uiautomator"]
    assert all(n.sources == ["dumpsys"] for n, _ in snap.root.walk())
    assert snap.package == "com.example.demo"


def test_same_app_dialog_window_is_not_merged():
    raw = views_raw()
    raw.uiautomator_xml = _window_xml("com.example.demo", "[100,800][980,1600]")
    snap = build_snapshot(raw, AT)
    assert "does not match" in snap.capabilities["uiautomator"]
    assert find_by_id(snap.root, "toolbar").bounds == Rect(0, 63, 1080, 210)


def test_landscape_swaps_wm_size():
    raw = views_raw()
    raw.dumpsys_text = None
    raw.uiautomator_xml = _window_xml("com.example.demo", "[0,0][2400,1080]", rotation=1)
    assert build_snapshot(raw, AT).screen == (2400, 1080)
