from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from layoutcli.adb import AdbError

DUMP_PATH = "/data/local/tmp/layoutcli_dump.xml"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class DeviceShell(Protocol):
    serial: str

    def exec_out(self, cmd: str, timeout: float = 30.0) -> bytes: ...


@dataclass
class RawCapture:
    device: dict[str, str] = field(default_factory=dict)
    dumpsys_text: str | None = None
    uiautomator_xml: str | None = None
    screenshot_png: bytes | None = None
    wm_size: str | None = None
    wm_density: str | None = None
    errors: dict[str, str] = field(default_factory=dict)


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace").replace("\r\n", "\n")


def _dump_uiautomator(adb: DeviceShell) -> str:
    out = _text(adb.exec_out(f"uiautomator dump {DUMP_PATH}", timeout=60.0))
    if "dumped to" not in out:
        raise AdbError(f"uiautomator dump failed: {out.strip() or 'no output'}")
    xml = _text(adb.exec_out(f"cat {DUMP_PATH}"))
    try:
        adb.exec_out(f"rm -f {DUMP_PATH}")
    except AdbError:
        pass
    return xml


def _screenshot(adb: DeviceShell) -> bytes:
    png = adb.exec_out("screencap -p", timeout=30.0)
    if not png.startswith(PNG_SIGNATURE):
        raise AdbError("screencap returned no PNG data")
    return png


def capture_raw(adb: DeviceShell) -> RawCapture:
    """Run every capture step; record failures in `errors` instead of raising."""
    raw = RawCapture(device={"serial": adb.serial})

    def attempt(key: str, fn):
        try:
            return fn()
        except AdbError as e:
            raw.errors[key] = str(e)
            return None

    for key, prop in (("model", "ro.product.model"), ("sdk", "ro.build.version.sdk")):
        value = attempt(f"getprop:{key}", lambda p=prop: _text(adb.exec_out(f"getprop {p}")).strip())
        if value:
            raw.device[key] = value
    raw.wm_size = attempt("wm size", lambda: _text(adb.exec_out("wm size")))
    raw.wm_density = attempt("wm density", lambda: _text(adb.exec_out("wm density")))
    raw.dumpsys_text = attempt("dumpsys", lambda: _text(adb.exec_out("dumpsys activity top")))
    raw.uiautomator_xml = attempt("uiautomator", lambda: _dump_uiautomator(adb))
    raw.screenshot_png = attempt("screenshot", lambda: _screenshot(adb))
    return raw


def pull_apk(adb: DeviceShell, package: str, dest: Path) -> Path:
    """Copy the installed base APK of `package` from the device to `dest`."""
    paths = [line[len("package:"):].strip() for line in _text(adb.exec_out(f"pm path {package}")).splitlines()
             if line.startswith("package:")]
    if not paths:
        raise AdbError(f"package {package} is not installed on the device")
    base = next((p for p in paths if p.endswith("/base.apk")), paths[0])
    data = adb.exec_out(f"cat {base}", timeout=600.0)
    if not data.startswith(b"PK"):
        raise AdbError(f"could not read {base}")
    dest.write_bytes(data)
    return dest
