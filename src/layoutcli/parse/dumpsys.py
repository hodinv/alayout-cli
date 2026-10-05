from __future__ import annotations

import re
from dataclasses import dataclass, field

from layoutcli.model import Rect

_VIEW_RE = re.compile(
    r"^(?P<cls>[\w.$]+)\{(?P<hash>[0-9a-f]+) (?P<f1>\S+) (?P<f2>\S+) "
    r"(?P<l>-?\d+),(?P<t>-?\d+)-(?P<r>-?\d+),(?P<b>-?\d+)"
    r"(?: #(?P<hexid>[0-9a-f]+)(?: (?P<resname>[^\s}]+))?)?"
    r"[^}]*\}"
)
_ROOT_RE = re.compile(r"^(?P<cls>[\w.$]+)@(?P<hash>[0-9a-f]+)")
_ACTIVITY_RE = re.compile(r"^\s*ACTIVITY (?P<comp>\S+)")


@dataclass(eq=False)
class DNode:
    class_name: str
    hash: str
    flags: str = ""
    rel: Rect | None = None  # relative to parent; None for the DecorView root line
    res_id: str | None = None  # e.g. "app:id/toolbar"
    children: list[DNode] = field(default_factory=list)

    @property
    def visibility(self) -> str:
        return {"I": "invisible", "G": "gone"}.get(self.flags[:1], "visible")

    @property
    def clickable(self) -> bool:
        return len(self.flags) > 6 and self.flags[6] == "C"


@dataclass
class DumpsysResult:
    package: str | None
    activity: str | None
    root: DNode
    resumed: bool = True  # False when no activity was resumed (e.g. lock screen) and we fell back


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _ends_hierarchy(line: str, header_indent: int) -> bool:
    """The View Hierarchy section ends at the next section at its own level or a new block.

    Lines indented less than the header that are not a new ACTIVITY/TASK block are
    continuations of custom multi-line View.toString() output (e.g. MIUI launcher).
    """
    if not line.strip():
        return False
    indent = _indent(line)
    if indent == header_indent:
        return True
    if indent < header_indent:
        return bool(_ACTIVITY_RE.match(line)) or line.startswith("TASK ")
    return False


def _split_component(comp: str) -> tuple[str | None, str | None]:
    if "/" not in comp:
        return comp, None
    package, activity = comp.split("/", 1)
    if activity.startswith("."):
        activity = package + activity
    return package, activity


def _parse_view_line(s: str) -> DNode | None:
    m = _VIEW_RE.match(s)
    if m:
        return DNode(
            class_name=m["cls"], hash=m["hash"], flags=f"{m['f1']} {m['f2']}",
            rel=Rect(int(m["l"]), int(m["t"]), int(m["r"]), int(m["b"])),
            res_id=m["resname"])
    m = _ROOT_RE.match(s)
    if m:
        return DNode(class_name=m["cls"], hash=m["hash"])
    return None


def _parse_hierarchy(body: list[str]) -> DNode | None:
    if not body:
        return None
    base = _indent(body[0])
    root: DNode | None = None
    stack: list[tuple[int, DNode]] = []
    for line in body:
        node = _parse_view_line(line.strip())
        if node is None:
            continue
        depth = (_indent(line) - base) // 2
        while stack and stack[-1][0] >= depth:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        elif root is None:
            root = node
        else:
            continue  # a second top-level root: ignore it
        stack.append((depth, node))
    return root


def parse_dumpsys(text: str) -> DumpsysResult | None:
    """Parse `dumpsys activity top`; return the resumed activity's View tree."""
    lines = text.splitlines()
    blocks: list[dict] = []
    current: dict | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _ACTIVITY_RE.match(line)
        if m:
            current = {"comp": m["comp"], "resumed": False, "root": None}
            blocks.append(current)
        elif current is not None:
            if "mResumed=true" in line:
                current["resumed"] = True
            elif line.strip() == "View Hierarchy:":
                header = _indent(line)
                j = i + 1
                body = []
                while j < len(lines) and not _ends_hierarchy(lines[j], header):
                    if lines[j].strip():
                        body.append(lines[j])
                    j += 1
                current["root"] = _parse_hierarchy(body)
                i = j
                continue
        i += 1
    with_root = [b for b in blocks if b["root"] is not None]
    if not with_root:
        return None
    chosen = next((b for b in with_root if b["resumed"]), with_root[-1])
    package, activity = _split_component(chosen["comp"])
    return DumpsysResult(package, activity, chosen["root"], resumed=chosen["resumed"])
