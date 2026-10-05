import pytest
from helpers import PNG_BYTES, FakeAdb, views_responses

from alayout.adb import AdbError
from alayout.capture import DUMP_PATH, capture_raw, pull_apk
from alayout.parse.wm import parse_wm_density, parse_wm_size


def test_wm_parsers_prefer_override():
    assert parse_wm_size("Physical size: 1080x2400\nOverride size: 720x1600\n") == (720, 1600)
    assert parse_wm_size("Physical size: 1080x2400\n") == (1080, 2400)
    assert parse_wm_size("garbage") is None
    assert parse_wm_density("Physical density: 420\nOverride density: 480\n") == 480
    assert parse_wm_density("") is None


def test_capture_collects_all_sources():
    adb = FakeAdb(views_responses())
    raw = capture_raw(adb)
    assert raw.device == {"serial": "emulator-5554", "model": "Pixel 7", "sdk": "34"}
    assert "View Hierarchy:" in raw.dumpsys_text
    assert raw.uiautomator_xml.startswith("<?xml")
    assert raw.screenshot_png == PNG_BYTES
    assert raw.wm_size.startswith("Physical size")
    assert raw.errors == {}
    assert f"rm -f {DUMP_PATH}" in adb.calls


def test_uiautomator_idle_failure_is_recorded_not_raised():
    responses = views_responses()
    responses[f"uiautomator dump {DUMP_PATH}"] = b"ERROR: could not get idle state.\n"
    raw = capture_raw(FakeAdb(responses))
    assert raw.uiautomator_xml is None
    assert raw.errors["uiautomator"] == "uiautomator dump failed: ERROR: could not get idle state."
    assert raw.dumpsys_text is not None
    assert raw.screenshot_png == PNG_BYTES


def test_adb_failure_in_one_source_does_not_stop_others():
    responses = views_responses()
    responses["dumpsys activity top"] = AdbError("boom")
    raw = capture_raw(FakeAdb(responses))
    assert raw.dumpsys_text is None
    assert raw.errors["dumpsys"] == "boom"
    assert raw.uiautomator_xml is not None


def test_screencap_without_png_signature_is_an_error():
    responses = views_responses()
    responses["screencap -p"] = b"Error: capture failed\n"
    raw = capture_raw(FakeAdb(responses))
    assert raw.screenshot_png is None
    assert "no PNG" in raw.errors["screenshot"]


def test_crlf_output_is_normalised():
    responses = views_responses()
    responses["dumpsys activity top"] = responses["dumpsys activity top"].replace(b"\n", b"\r\n")
    raw = capture_raw(FakeAdb(responses))
    assert "\r" not in raw.dumpsys_text


def test_pull_apk_writes_base_apk(tmp_path):
    responses = views_responses()
    responses["pm path com.example.demo"] = b"package:/data/app/~~x==/com.example.demo-y==/base.apk\npackage:/data/app/~~x==/split_config.arm64_v8a.apk\n"
    responses["cat /data/app/~~x==/com.example.demo-y==/base.apk"] = b"PK\x03\x04apk-bytes"
    dest = pull_apk(FakeAdb(responses), "com.example.demo", tmp_path / "base.apk")
    assert dest.read_bytes() == b"PK\x03\x04apk-bytes"


def test_pull_apk_unknown_package(tmp_path):
    with pytest.raises(AdbError, match="not installed"):
        pull_apk(FakeAdb(views_responses()), "com.missing", tmp_path / "base.apk")


def test_pull_apks_fetches_base_and_splits(tmp_path):
    from alayout.capture import pull_apks
    responses = views_responses()
    responses["pm path com.example.demo"] = (b"package:/data/app/x/split_feature_shop.apk\n"
                                             b"package:/data/app/x/base.apk\n")
    responses["cat /data/app/x/base.apk"] = b"PK base"
    responses["cat /data/app/x/split_feature_shop.apk"] = b"PK shop"
    paths = pull_apks(FakeAdb(responses), "com.example.demo", tmp_path)
    assert [p.name for p in paths] == ["base.apk", "split_feature_shop.apk"]
    assert paths[1].read_bytes() == b"PK shop"
