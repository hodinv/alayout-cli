from pathlib import Path

import pytest

from alayout.adb import Adb, AdbError, list_devices, select_device

ADB = Path("adb")


def fake_runner(outputs):
    calls = []

    def run(args, timeout):
        calls.append(args)
        return outputs[tuple(args[1:])]

    run.calls = calls
    return run


def test_list_devices_ignores_daemon_banner():
    out = (b"* daemon not running; starting now at tcp:5037\n* daemon started successfully\n"
           b"List of devices attached\nemulator-5554\tdevice\nR58M123\tunauthorized\n\n")
    runner = fake_runner({("devices",): (0, out, b"")})
    assert list_devices(ADB, runner) == [("emulator-5554", "device"), ("R58M123", "unauthorized")]


def test_single_device_is_selected_automatically():
    assert select_device([("emulator-5554", "device")], None) == "emulator-5554"


def test_multiple_devices_require_serial():
    with pytest.raises(AdbError) as exc:
        select_device([("a1", "device"), ("b2", "device")], None)
    assert "a1" in str(exc.value) and "b2" in str(exc.value) and "-s" in str(exc.value)


def test_no_device_mentions_unauthorized_one():
    with pytest.raises(AdbError, match="R58M123 is unauthorized"):
        select_device([("R58M123", "unauthorized")], None)


def test_no_device_at_all():
    with pytest.raises(AdbError, match="no Android device connected"):
        select_device([], None)


def test_explicit_serial_must_exist_and_be_ready():
    devices = [("a1", "device"), ("b2", "offline")]
    assert select_device(devices, "a1") == "a1"
    with pytest.raises(AdbError, match="'zz' not found"):
        select_device(devices, "zz")
    with pytest.raises(AdbError, match="'b2' is offline"):
        select_device(devices, "b2")


def test_connect_and_exec_out_pass_serial():
    runner = fake_runner({
        ("devices",): (0, b"List of devices attached\nemu\tdevice\n", b""),
        ("-s", "emu", "exec-out", "wm size"): (0, b"Physical size: 1080x2400\n", b""),
    })
    adb = Adb.connect(ADB, None, runner)
    assert adb.serial == "emu"
    assert adb.exec_out("wm size") == b"Physical size: 1080x2400\n"


def test_exec_out_nonzero_exit_raises_with_stderr():
    runner = fake_runner({("-s", "emu", "exec-out", "bad"): (1, b"", b"/system/bin/sh: bad: not found\n")})
    with pytest.raises(AdbError, match="bad: not found"):
        Adb(ADB, "emu", runner).exec_out("bad")
