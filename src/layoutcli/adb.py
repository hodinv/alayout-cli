from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Mapping

ADB_NAME = "adb.exe" if os.name == "nt" else "adb"


class AdbError(Exception):
    """Any failure to locate adb or talk to a device."""


_PROP_RE = re.compile(r"^\s*([^=:\s]+)\s*[=:]\s*(.*)$")


def _parse_properties(text: str) -> dict[str, str]:
    """Minimal Java .properties reader: comments, key=value, backslash escapes."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "#!":
            continue
        m = _PROP_RE.match(line)
        if m:
            result[m.group(1)] = re.sub(r"\\(.)", r"\1", m.group(2).strip())
    return result


def sdk_dir_from_local_properties(start: Path) -> Path | None:
    for directory in [start, *start.parents]:
        props_file = directory / "local.properties"
        if props_file.is_file():
            sdk = _parse_properties(props_file.read_text(encoding="utf-8")).get("sdk.dir")
            return Path(sdk) if sdk else None
    return None


def _default_sdk_dirs(env: Mapping[str, str]) -> list[Path]:
    dirs: list[Path] = []
    if env.get("LOCALAPPDATA"):
        dirs.append(Path(env["LOCALAPPDATA"]) / "Android" / "Sdk")
    home = env.get("HOME") or env.get("USERPROFILE")
    if home:
        dirs += [Path(home) / "Library" / "Android" / "sdk", Path(home) / "Android" / "Sdk"]
    return dirs


def find_adb(
    explicit: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd

    if explicit:
        path = Path(explicit)
        if path.is_dir():
            path = path / ADB_NAME
        if path.is_file():
            return path
        raise AdbError(f"adb not found at {explicit}")

    tried: list[str] = []

    def in_sdk(sdk: Path) -> Path | None:
        candidate = sdk / "platform-tools" / ADB_NAME
        tried.append(str(candidate))
        return candidate if candidate.is_file() else None

    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if env.get(var) and (found := in_sdk(Path(env[var]))):
            return found
    sdk = sdk_dir_from_local_properties(cwd)
    if sdk and (found := in_sdk(sdk)):
        return found
    on_path = which("adb")
    if on_path:
        return Path(on_path)
    tried.append("PATH")
    for default in _default_sdk_dirs(env):
        if found := in_sdk(default):
            return found
    raise AdbError(
        "adb not found (tried: " + ", ".join(tried) + "). "
        "Use --adb PATH, set ANDROID_HOME, or run inside a project with local.properties.")


Runner = Callable[[list[str], float], tuple[int, bytes, bytes]]


def _subprocess_runner(args: list[str], timeout: float) -> tuple[int, bytes, bytes]:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise AdbError(f"cannot run adb: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise AdbError(f"adb timed out after {timeout:g}s: {' '.join(args[1:])}") from e
    return proc.returncode, proc.stdout, proc.stderr


def list_devices(adb_path: Path, runner: Runner) -> list[tuple[str, str]]:
    code, out, err = runner([str(adb_path), "devices"], 15.0)
    if code != 0:
        raise AdbError(f"`adb devices` failed: {err.decode('utf-8', 'replace').strip()}")
    devices = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))
    return devices


def select_device(devices: list[tuple[str, str]], serial: str | None) -> str:
    if serial is not None:
        for s, state in devices:
            if s == serial:
                if state != "device":
                    raise AdbError(f"device '{serial}' is {state}")
                return s
        known = ", ".join(s for s, _ in devices) or "none"
        raise AdbError(f"device '{serial}' not found; connected: {known}")
    ready = [s for s, state in devices if state == "device"]
    if len(ready) == 1:
        return ready[0]
    if len(ready) > 1:
        raise AdbError(f"multiple devices connected ({', '.join(ready)}): choose one with -s SERIAL")
    not_ready = [f"{s} is {state}" for s, state in devices]
    if not_ready:
        hint = " (accept the USB debugging prompt on the device)" if any(
            st == "unauthorized" for _, st in devices) else ""
        raise AdbError("no usable Android device: " + ", ".join(not_ready) + hint)
    raise AdbError("no Android device connected (check `adb devices`)")


class Adb:
    def __init__(self, adb_path: Path, serial: str, runner: Runner = _subprocess_runner):
        self.adb_path = adb_path
        self.serial = serial
        self._runner = runner

    @classmethod
    def connect(cls, adb_path: Path, serial: str | None = None,
                runner: Runner = _subprocess_runner) -> Adb:
        return cls(adb_path, select_device(list_devices(adb_path, runner), serial), runner)

    def exec_out(self, cmd: str, timeout: float = 30.0) -> bytes:
        code, out, err = self._runner(
            [str(self.adb_path), "-s", self.serial, "exec-out", cmd], timeout)
        if code != 0:
            detail = (err or out).decode("utf-8", "replace").strip()
            raise AdbError(f"`{cmd}` failed: {detail}")
        return out
