from __future__ import annotations

from pathlib import Path

from layoutcli.build import build_snapshot
from layoutcli.capture import DUMP_PATH, RawCapture
from layoutcli.model import ViewNode

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def find_by_id(root: ViewNode, view_id: str) -> ViewNode:
    for node, _ in root.walk():
        if node.id == view_id:
            return node
    raise AssertionError(f"no node with id {view_id!r}")


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeAdb:
    def __init__(self, responses: dict, serial: str = "emulator-5554"):
        self.responses = responses
        self.serial = serial
        self.calls: list[str] = []

    def exec_out(self, cmd: str, timeout: float = 30.0) -> bytes:
        self.calls.append(cmd)
        response = self.responses.get(cmd, b"")
        if isinstance(response, Exception):
            raise response
        return response


def views_responses() -> dict:
    return {
        "getprop ro.product.model": b"Pixel 7\n",
        "getprop ro.build.version.sdk": b"34\n",
        "wm size": b"Physical size: 1080x2400\n",
        "wm density": b"Physical density: 420\n",
        "dumpsys activity top": (FIXTURES / "views_dumpsys.txt").read_bytes(),
        f"uiautomator dump {DUMP_PATH}": f"UI hierchary dumped to: {DUMP_PATH}\n".encode(),
        f"cat {DUMP_PATH}": (FIXTURES / "views_uiautomator.xml").read_bytes(),
        f"rm -f {DUMP_PATH}": b"",
        "screencap -p": PNG_BYTES,
    }


def views_raw() -> RawCapture:
    return RawCapture(
        device={"serial": "emulator-5554", "model": "Pixel 7", "sdk": "34"},
        dumpsys_text=read_fixture("views_dumpsys.txt"),
        uiautomator_xml=read_fixture("views_uiautomator.xml"),
        screenshot_png=PNG_BYTES,
        wm_size="Physical size: 1080x2400\n",
        wm_density="Physical density: 420\n",
    )


def views_snapshot():
    return build_snapshot(views_raw(), captured_at="2026-10-04T10:00:00+00:00")
