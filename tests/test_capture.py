from helpers import PNG_BYTES, FakeAdb, views_responses

from layoutcli.adb import AdbError
from layoutcli.capture import DUMP_PATH, capture_raw
from layoutcli.parse.wm import parse_wm_density, parse_wm_size


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
