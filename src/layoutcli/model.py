from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

FORMAT_VERSION = 1


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def offset(self, dx: int, dy: int) -> Rect:
        return Rect(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def to_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]

    @classmethod
    def from_list(cls, values: list[int]) -> Rect:
        return cls(*values)

    def __str__(self) -> str:
        return f"[{self.left},{self.top}][{self.right},{self.bottom}]"


def short_id(res: str | None) -> str | None:
    """'com.example:id/title' or 'app:id/title' -> 'title'."""
    if not res:
        return None
    return res.rsplit("/", 1)[-1]


@dataclass(eq=False)
class ViewNode:
    class_name: str
    id: str | None = None
    bounds: Rect | None = None  # absolute screen pixels
    visibility: str = "visible"  # visible | invisible | gone
    text: str | None = None
    sources: list[str] = field(default_factory=list)
    props: dict[str, dict[str, str]] = field(default_factory=dict)  # source -> name -> value
    children: list[ViewNode] = field(default_factory=list)

    @property
    def short_class(self) -> str:
        return self.class_name.rsplit(".", 1)[-1]

    def walk(self, depth: int = 0) -> Iterator[tuple[ViewNode, int]]:
        yield self, depth
        for child in self.children:
            yield from child.walk(depth + 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": self.class_name,
            "id": self.id,
            "bounds": self.bounds.to_list() if self.bounds else None,
            "visibility": self.visibility,
            "text": self.text,
            "sources": list(self.sources),
            "props": {k: dict(v) for k, v in self.props.items()},
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ViewNode:
        return cls(
            class_name=d["class"],
            id=d.get("id"),
            bounds=Rect.from_list(d["bounds"]) if d.get("bounds") else None,
            visibility=d.get("visibility", "visible"),
            text=d.get("text"),
            sources=list(d.get("sources", [])),
            props={k: dict(v) for k, v in d.get("props", {}).items()},
            children=[cls.from_dict(c) for c in d.get("children", [])],
        )


@dataclass(eq=False)
class Snapshot:
    root: ViewNode
    screen: tuple[int, int]
    density: int | None
    package: str | None = None
    activity: str | None = None
    device: dict[str, str] = field(default_factory=dict)
    captured_at: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)
    screenshot: str | None = None  # file name inside the snapshot dir

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_VERSION,
            "package": self.package,
            "activity": self.activity,
            "device": dict(self.device),
            "captured_at": self.captured_at,
            "screen": list(self.screen),
            "density": self.density,
            "capabilities": dict(self.capabilities),
            "screenshot": self.screenshot,
            "root": self.root.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        if d.get("format") != FORMAT_VERSION:
            raise ValueError(f"unsupported snapshot format {d.get('format')!r}")
        return cls(
            root=ViewNode.from_dict(d["root"]),
            screen=(int(d["screen"][0]), int(d["screen"][1])),
            density=d.get("density"),
            package=d.get("package"),
            activity=d.get("activity"),
            device=dict(d.get("device", {})),
            captured_at=d.get("captured_at", ""),
            capabilities=dict(d.get("capabilities", {})),
            screenshot=d.get("screenshot"),
        )
