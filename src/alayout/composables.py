from __future__ import annotations

import re
import struct
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

LIBRARY_PREFIXES = ("androidx.", "android.", "kotlin.", "kotlinx.", "com.google.", "org.jetbrains.")
_DEX_RE = re.compile(r"classes\d*\.dex")
_NAME_RE = re.compile(r"^C\(([A-Za-z_][\w$]*)\)")  # "CC(" marks inline call sites, not definitions
_FILE_RE = re.compile(r":([^:#]+\.kt)#")
_LINE_RE = re.compile(r"(\d+)@\d+L\d+")
_HASH_RE = re.compile(r"\.kt#(\w+)")
_PREVIEW_RE = re.compile(r"Prev(iew)?(?![a-z])")


@dataclass(frozen=True)
class ComposableInfo:
    """A composable function found in the APK's Compose source information (debug builds keep it)."""

    name: str
    file: str
    line: int | None  # approximate: first call inside the function (1-based)
    package: str | None

    @property
    def preview(self) -> bool:
        return bool(_PREVIEW_RE.search(self.name))


def _uleb128(data: bytes, pos: int) -> int:
    while data[pos] & 0x80:
        pos += 1
    return pos + 1


def dex_strings(data: bytes) -> list[str]:
    count, offset = struct.unpack_from("<II", data, 56)
    strings = []
    for i in range(count):
        (start,) = struct.unpack_from("<I", data, offset + 4 * i)
        begin = _uleb128(data, start)
        end = data.index(0, begin)
        strings.append(data[begin:end].decode("utf-8", "replace"))
    return strings


def dex_class_sources(data: bytes, strings: list[str] | None = None) -> list[tuple[str, str]]:
    """(class descriptor, source file name) for every class definition."""
    strings = dex_strings(data) if strings is None else strings
    type_count, type_off = struct.unpack_from("<II", data, 64)
    types = [struct.unpack_from("<I", data, type_off + 4 * i)[0] for i in range(type_count)]
    class_count, class_off = struct.unpack_from("<II", data, 96)
    result = []
    for i in range(class_count):
        class_idx, _, _, _, source_idx = struct.unpack_from("<5I", data, class_off + 32 * i)
        if source_idx < len(strings) and class_idx < len(types):
            result.append((strings[types[class_idx]], strings[source_idx]))
    return result


def parse_source_information(text: str) -> tuple[str, str, int | None] | None:
    name, file = _NAME_RE.match(text), _FILE_RE.search(text)
    if not name or not file:
        return None
    line = _LINE_RE.search(text)
    return name.group(1), file.group(1), int(line.group(1)) + 1 if line else None


def _package(descriptor: str) -> str:
    return descriptor.strip("L;").rsplit("/", 1)[0].replace("/", ".")


def list_composables(apk_paths: Path | list[Path]) -> list[ComposableInfo]:
    """Composables in the APK(s); pass split APKs too, dynamic features keep their code there."""
    entries: list[tuple[str, str, int | None, str]] = []
    packages: dict[str, set[str]] = {}
    for apk_path in [apk_paths] if isinstance(apk_paths, Path) else apk_paths:
        with zipfile.ZipFile(apk_path) as apk:
            for name in apk.namelist():
                if not _DEX_RE.fullmatch(name):
                    continue
                data = apk.read(name)
                strings = dex_strings(data)
                for descriptor, source in dex_class_sources(data, strings):
                    packages.setdefault(source, set()).add(_package(descriptor))
                for text in strings:
                    parsed = parse_source_information(text)
                    if parsed:
                        h = _HASH_RE.search(text)
                        entries.append((*parsed, h.group(1) if h else ""))
    # The "#hash" suffix identifies the compiled package; it separates a library LazyDsl.kt from
    # app classes whose inlined code also carries the source name LazyDsl.kt.
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    for _, file, _, h in entries:
        votes[h].update(packages.get(file, ()))
    found: dict[tuple[str, str], ComposableInfo] = {}
    for name, file, line, h in entries:
        options = packages.get(file, set())
        package = max(options, key=lambda p: (votes[h][p], not p.startswith(LIBRARY_PREFIXES), p)) if options else None
        found.setdefault((name, file), ComposableInfo(name, file, line, package))
    return sorted(found.values(), key=lambda c: (c.package or "", c.file, c.line or 0, c.name))


def app_composables(items: list[ComposableInfo], package: str | None) -> list[ComposableInfo]:
    """The app's own composables: in the app package when known, otherwise anything outside libraries."""
    if package:
        own = [i for i in items if i.package and (i.package == package or i.package.startswith(package + "."))]
        if own:
            return own
    return [i for i in items if i.package and not i.package.startswith(LIBRARY_PREFIXES)]
