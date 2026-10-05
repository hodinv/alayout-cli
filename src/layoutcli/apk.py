from __future__ import annotations

import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping
from xml.sax.saxutils import quoteattr

from layoutcli.model import ViewNode

AAPT2_NAME = "aapt2.exe" if os.name == "nt" else "aapt2"
_RESOURCE_RE = re.compile(r"^\s*resource (0x[0-9a-f]+) (\S+)")
_FILE_RE = re.compile(r"\(file\) (\S+) type=XML")
_ELEMENT_RE = re.compile(r"^(\s*)E: (\S+)")
_ATTR_RE = re.compile(r"^\s*A: (\S+?)\(0x[0-9a-f]+\)=(.*)$|^\s*A: ([^=(]+)=(.*)$")
_NAMESPACES = {"http://schemas.android.com/apk/res/android": "android",
               "http://schemas.android.com/apk/res-auto": "app",
               "http://schemas.android.com/tools": "tools"}
_LAYOUT_SIZES = {"-1": "match_parent", "-2": "wrap_content"}


class ApkError(Exception):
    pass


def _version_key(path: Path) -> tuple:
    return tuple(int(p) if p.isdigit() else -1 for p in re.split(r"[.-]", path.parent.name))


def find_aapt2(adb_path: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    sdks: list[Path] = []
    if adb_path is not None:
        sdks.append(Path(adb_path).parent.parent)
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if env.get(var):
            sdks.append(Path(env[var]))
    if env.get("LOCALAPPDATA"):
        sdks.append(Path(env["LOCALAPPDATA"]) / "Android" / "Sdk")
    for sdk in sdks:
        found = sorted((sdk / "build-tools").glob(f"*/{AAPT2_NAME}"), key=_version_key, reverse=True)
        if found:
            return found[0]
    raise ApkError("aapt2 not found (install Android SDK build-tools); tried: "
                   + ", ".join(str(s / "build-tools") for s in sdks))


def parse_resources_dump(text: str) -> tuple[dict[str, str], list[str]]:
    names: dict[str, str] = {}
    layouts: list[str] = []
    current = ""
    for line in text.splitlines():
        m = _RESOURCE_RE.match(line)
        if m:
            names[m.group(1)] = current = m.group(2)
            continue
        f = _FILE_RE.search(line)
        if f and current.startswith("layout/"):
            layouts.append(f.group(1))
    return names, layouts


def _attr_name(raw: str) -> str:
    if ":" in raw and raw.startswith("http"):
        uri, local = raw.rsplit(":", 1)
        return f"{_NAMESPACES.get(uri, uri.rsplit('/', 1)[-1])}:{local}"
    return raw


def _attr_value(name: str, raw: str, names: dict[str, str]) -> str:
    raw = raw.strip()
    if raw.startswith("@0x") or raw.startswith("?0x"):
        ref = names.get(raw[1:])
        return f"{raw[0]}{ref}" if ref else raw
    if raw.startswith('"'):
        m = re.match(r'"((?:[^"\\]|\\.)*)"', raw)
        return m.group(1) if m else raw
    if name.endswith(("layout_width", "layout_height")) and raw in _LAYOUT_SIZES:
        return _LAYOUT_SIZES[raw]
    m = re.fullmatch(r"(-?\d+)\.0+(dp|sp|px|dip)", raw)
    if m:
        return m.group(1) + m.group(2)
    return raw


@dataclass
class _Element:
    tag: str
    indent: int
    attrs: list[tuple[str, str]] = field(default_factory=list)
    children: list[_Element] = field(default_factory=list)


def parse_xmltree(text: str, names: dict[str, str]) -> tuple[str, list[str]]:
    roots: list[_Element] = []
    stack: list[_Element] = []
    ids: list[str] = []
    for line in text.splitlines():
        m = _ELEMENT_RE.match(line)
        if m:
            element = _Element(m.group(2), len(m.group(1)))
            while stack and stack[-1].indent >= element.indent:
                stack.pop()
            (stack[-1].children if stack else roots).append(element)
            stack.append(element)
            continue
        a = _ATTR_RE.match(line)
        if a and stack:
            raw_name = a.group(1) or a.group(3)
            raw_value = a.group(2) if a.group(1) else a.group(4)
            name = _attr_name(raw_name.strip())
            value = _attr_value(name, raw_value, names)
            stack[-1].attrs.append((name, value))
            if name == "android:id" and value.startswith("@id/"):
                ids.append(value[4:])
    lines: list[str] = []

    def emit(element: _Element, depth: int) -> None:
        attrs = "".join(f" {k}={quoteattr(v)}" for k, v in element.attrs)
        pad = "    " * depth
        if element.children:
            lines.append(f"{pad}<{element.tag}{attrs}>")
            for child in element.children:
                emit(child, depth + 1)
            lines.append(f"{pad}</{element.tag}>")
        else:
            lines.append(f"{pad}<{element.tag}{attrs}/>")

    for root in roots:
        emit(root, 0)
    return "\n".join(lines), ids


@dataclass
class ApkIndex:
    ids: dict[str, list[str]] = field(default_factory=dict)
    layouts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ids": {k: list(v) for k, v in self.ids.items()}, "layouts": dict(self.layouts)}

    @classmethod
    def from_dict(cls, d: dict) -> ApkIndex:
        return cls(ids={k: list(v) for k, v in d.get("ids", {}).items()}, layouts=dict(d.get("layouts", {})))

    def restricted_to(self, ids: set[str]) -> ApkIndex:
        kept = {k: v for k, v in self.ids.items() if k in ids}
        files = {f for v in kept.values() for f in v}
        return ApkIndex(kept, {f: x for f, x in self.layouts.items() if f in files})


def _run(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ApkError(f"cannot run aapt2: {e}") from e
    if proc.returncode != 0:
        raise ApkError(f"aapt2 failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def build_index(apk_path: Path, aapt2: Path, runner: Callable[[list[str]], str] | None = None) -> ApkIndex:
    run = runner or _run
    names, layout_files = parse_resources_dump(run([str(aapt2), "dump", "resources", str(apk_path)]))

    def decode(file: str) -> tuple[str, str, list[str]]:
        xml, ids = parse_xmltree(run([str(aapt2), "dump", "xmltree", "--file", file, str(apk_path)]), names)
        return file, xml, ids

    with ThreadPoolExecutor(max_workers=8) as pool:
        decoded = list(pool.map(decode, layout_files))
    index = ApkIndex()
    for file, xml, ids in decoded:
        if not ids:
            continue  # e.g. an unversioned copy without attributes
        index.layouts[file] = xml
        for view_id in ids:
            index.ids.setdefault(view_id, [])
            if file not in index.ids[view_id]:
                index.ids[view_id].append(file)
    return index


def _is_framework_id(node: ViewNode) -> bool:
    """android:id/... views belong to the platform; the APK index only knows the app's own ids."""
    full = (node.props.get("dumpsys", {}).get("resource_id")
            or node.props.get("uiautomator", {}).get("resource-id") or "")
    return full.startswith("android:")


def apply_index(root: ViewNode, index: ApkIndex) -> int:
    mapped = 0
    for node, _ in root.walk():
        if node.id and node.id in index.ids and not _is_framework_id(node):
            node.props["apk"] = {"layouts": ", ".join(index.ids[node.id])}
            mapped += 1
    return mapped
