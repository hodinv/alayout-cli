import json
from pathlib import Path

from helpers import PNG_BYTES, FakeAdb, views_raw, views_responses, views_snapshot
from typer.testing import CliRunner

from alayout import cli
from alayout.adb import _parse_properties, list_devices
from alayout.apk import _attr_value
from alayout.capture import capture_raw
from alayout.checks import run_checks
from alayout.diff import diff_snapshots
from alayout.model import Rect, Snapshot, ViewNode
from alayout.snapshot_io import APK_FILE, SCREEN_FILE, save_capture


def test_properties_decode_unicode_escapes():
    props = _parse_properties("sdk.dir=C\\:\\\\Users\\\\\\u0412\\u0430\\u0441\\\\Sdk\n")
    assert props["sdk.dir"] == "C:\\Users\\Вас\\Sdk"


def test_device_state_with_spaces_is_kept_whole():
    out = b"List of devices attached\nABC123\tno permissions (user in plugdev group); see [http://x]\n"
    devices = list_devices(Path("adb"), lambda args, timeout: (0, out, b""))
    assert devices == [("ABC123", "no permissions (user in plugdev group); see [http://x]")]


def test_screencap_warning_before_png_is_stripped():
    responses = views_responses()
    responses["screencap -p"] = b"[Warning] Multiple displays were found, defaulting to first\n" + PNG_BYTES
    raw = capture_raw(FakeAdb(responses))
    assert raw.screenshot_png == PNG_BYTES
    assert "screenshot" not in raw.errors


def test_recapture_into_same_dir_removes_stale_files(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    (tmp_path / APK_FILE).write_text("{}", encoding="utf-8")
    raw = views_raw()
    raw.screenshot_png = None
    raw.uiautomator_xml = None
    snap = views_snapshot()
    save_capture(raw, snap, tmp_path)
    assert not (tmp_path / SCREEN_FILE).exists()
    assert not (tmp_path / APK_FILE).exists()
    assert not (tmp_path / "raw" / "uiautomator.xml").exists()
    assert snap.screenshot is None


def test_framework_references_are_named():
    assert _attr_value("android:textAppearance", "?0x01010041", {}) == "?android:attr/0x01010041"
    assert _attr_value("android:text", "@0x0104000a", {}) == "@android:0x0104000a"
    assert _attr_value("android:text", "@0x7f0e0001", {}) == "@0x7f0e0001"  # unknown app ref: left alone


def _leaf(**kw) -> ViewNode:
    return ViewNode(class_name="android.widget.TextView", sources=["dumpsys"], **kw)


def test_bounds_missing_on_one_side_is_not_a_change():
    a = ViewNode(class_name="F", bounds=Rect(0, 0, 10, 10), children=[_leaf(id="t", bounds=Rect(0, 0, 5, 5))])
    b = ViewNode(class_name="F", bounds=Rect(0, 0, 10, 10), children=[_leaf(id="t", bounds=None)])
    assert diff_snapshots(a, b).empty


def _snap(root: ViewNode) -> Snapshot:
    return Snapshot(root=root, screen=(1000, 2000), density=160)


def test_gone_child_text_does_not_label_a_clickable():
    gone_text = ViewNode(class_name="android.widget.TextView", text="Hidden", visibility="gone",
                         bounds=Rect(0, 0, 0, 0), props={"uiautomator": {}})
    button = ViewNode(class_name="android.view.View", bounds=Rect(0, 0, 200, 200),
                      props={"uiautomator": {"clickable": "true"}}, children=[gone_text])
    issues = run_checks(_snap(ViewNode(class_name="F", bounds=Rect(0, 0, 1000, 2000), children=[button])))
    assert any(i.check == "missing-label" and i.node is button for i in issues)


def test_deep_nesting_reported_once_per_parent():
    leaves = [ViewNode(class_name="L", bounds=Rect(0, 0, 100, 100)) for _ in range(5)]
    node = ViewNode(class_name="P", bounds=Rect(0, 0, 100, 100), children=leaves)
    for _ in range(10):
        node = ViewNode(class_name="P", bounds=Rect(0, 0, 100, 100), children=[node])
    deep = [i for i in run_checks(_snap(node)) if i.check == "deep-nesting"]
    assert len(deep) == 1
    assert "5 views" in deep[0].message


runner = CliRunner()


def test_capture_to_unwritable_out_is_a_clean_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    result = runner.invoke(cli.app, ["capture", "-o", str(blocker / "sub")])
    assert result.exit_code == 1
    assert "error" in result.output and "Traceback" not in result.output


def test_apk_option_before_command_is_used(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_capture",
                        lambda adb, serial, out, apk=None, compose=False, signing=None, show_ghosts=False:
                        seen.update(apk=apk, compose=compose) or tmp_path)
    runner.invoke(cli.app, ["--apk", "device", "--compose", "capture"])
    assert seen["apk"] == "device"
    assert seen["compose"] is True


def test_apk_option_for_existing_snapshot_warns(tmp_path, monkeypatch):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    monkeypatch.setattr(cli.LayoutApp, "run", lambda self: None)
    result = runner.invoke(cli.app, ["inspect", str(tmp_path), "--apk", "device"])
    assert result.exit_code == 0
    assert "--apk" in result.output and "ignored" in result.output


def test_apk_write_failure_is_recorded_not_raised(tmp_path, monkeypatch):
    def broken(adb, apk, package):
        raise OSError("disk full")
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    monkeypatch.setattr(cli, "_apk_index", broken)
    result = runner.invoke(cli.app, ["capture", "-o", str(tmp_path / "s"), "--apk", "device"])
    assert result.exit_code == 0, result.output
    caps = json.loads((tmp_path / "s" / "snapshot.json").read_text(encoding="utf-8"))["capabilities"]
    assert "disk full" in caps["apk"]
