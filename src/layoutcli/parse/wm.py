from __future__ import annotations

import re

_SIZE_RE = re.compile(r"(Physical|Override) size: (\d+)x(\d+)")
_DENSITY_RE = re.compile(r"(Physical|Override) density: (\d+)")


def parse_wm_size(text: str) -> tuple[int, int] | None:
    found = {m[1]: (int(m[2]), int(m[3])) for m in _SIZE_RE.finditer(text)}
    return found.get("Override") or found.get("Physical")


def parse_wm_density(text: str) -> int | None:
    found = {m[1]: int(m[2]) for m in _DENSITY_RE.finditer(text)}
    return found.get("Override") or found.get("Physical")
