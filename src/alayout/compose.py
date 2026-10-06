from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from alayout.checks import is_clickable
from alayout.model import Rect, ViewNode

if TYPE_CHECKING:
    from alayout.agent import ComposeHit

TOGGLE_CLASSES = ("CheckBox", "Switch", "RadioButton", "ToggleButton")


@dataclass(frozen=True)
class Component:
    """What a Compose semantics node most likely is. Inferred from semantics: composable names are not available."""

    kind: str
    label: str | None = None
    state: tuple[str, ...] = ()
    repeat: tuple[int, int] | None = None  # (index, count) among structurally identical siblings

    def describe(self) -> str:
        text = self.kind
        if self.label:
            text += f' "{self.label}"'
        if self.state:
            text += " [" + ", ".join(self.state) + "]"
        if self.repeat:
            text += f" ({self.repeat[0]} of {self.repeat[1]} similar)"
        return text


def _ui(node: ViewNode) -> dict[str, str]:
    return node.props.get("uiautomator", {})


def _text_label(node: ViewNode) -> str:
    texts: list[str] = []

    def visit(n: ViewNode) -> None:
        if n.visibility != "visible":
            return
        if n.text:
            texts.append(n.text.strip())
        for child in n.children:
            visit(child)

    visit(node)
    return " / ".join(t for t in texts if t)


def _state(ui: dict[str, str]) -> tuple[str, ...]:
    state = []
    if ui.get("checkable") == "true":
        state.append("checked" if ui.get("checked") == "true" else "unchecked")
    if ui.get("selected") == "true":
        state.append("selected")
    if ui.get("enabled") == "false":
        state.append("disabled")
    return tuple(state)


def _classify(node: ViewNode, ancestors: tuple[ViewNode, ...]) -> Component | None:
    ui = _ui(node)
    cls = node.short_class
    clickable = is_clickable(node)
    desc = ui.get("content-desc") or None
    label = _text_label(node) or desc
    state = _state(ui)
    parent = ancestors[-1] if ancestors else None

    if cls == "EditText":
        return Component("TextField", node.text or desc, state)
    if cls in TOGGLE_CLASSES:
        return Component(cls, label, state)
    if ui.get("checkable") == "true":
        return Component("Toggle", label, state)
    if cls == "Button":
        if parent is not None and is_clickable(parent) and parent.bounds == node.bounds:
            return None  # role marker of the clickable parent, which is reported as the Button
        return Component("Button", label, state)
    if clickable and any(c.short_class == "Button" and c.bounds == node.bounds for c in node.children):
        return Component("Button", label, state)
    if clickable:
        text = _text_label(node)
        if text:
            return Component("Clickable", text, state)
        if desc:
            return Component("IconButton", desc, state)
        owner = next((a for a in reversed(ancestors) if is_clickable(a) and _text_label(a)), None)
        return Component("Selector" if owner is not None else "Clickable", None, state)
    if ui.get("scrollable") == "true":
        return Component("Scrollable", None, state)
    return None


def _shape(node: ViewNode) -> tuple:
    return (node.short_class, tuple(_shape(c) for c in node.children))


def _region(root: ViewNode) -> list[tuple[ViewNode, tuple[ViewNode, ...]]]:
    """Semantics nodes inside Compose hosts (ComposeView / AndroidComposeView), with their ancestors."""
    found: list[tuple[ViewNode, tuple[ViewNode, ...]]] = []

    def walk(node: ViewNode, ancestors: tuple[ViewNode, ...], in_compose: bool) -> None:
        name = node.class_name
        if name.endswith("AndroidViewsHandler"):
            return  # interop Views hosted by Compose are regular Views
        is_host = name.endswith("ComposeView")
        if in_compose and not is_host and "uiautomator" in node.props:
            found.append((node, ancestors))
        for child in node.children:
            walk(child, ancestors + (node,), in_compose or is_host)

    walk(root, (), False)
    return found


def compose_nodes(root: ViewNode) -> set[ViewNode]:
    found = {node for node, _ in _region(root)}
    found.update(node for node, _ in root.walk() if "compose" in node.props)
    return found


def infer_components(root: ViewNode) -> dict[ViewNode, Component]:
    """Component guesses for the semantics nodes inside Compose hosts."""
    found: dict[ViewNode, Component] = {}
    parents: dict[ViewNode, ViewNode] = {}
    for node, ancestors in _region(root):
        component = _classify(node, ancestors)
        if component is not None:
            found[node] = component
            if ancestors:
                parents[node] = ancestors[-1]

    groups: dict[tuple, list[ViewNode]] = defaultdict(list)
    for node, component in found.items():
        if node in parents:
            groups[(id(parents[node]), component.kind, _shape(node))].append(node)
    for members in groups.values():
        if len(members) > 1:
            for index, node in enumerate(members, start=1):
                c = found[node]
                found[node] = Component(c.kind, c.label, c.state, (index, len(members)))
    return found


STRUCTURAL_COMPOSABLES = frozenset({
    "Layout", "ReusableComposeNode", "ReusableContent", "SubcomposeLayout", "LazyLayout",
    "SaveableStateProvider", "LazySaveableStateHolderProvider", "LocalOwnersProvider",
    "CompositionLocalProvider", "ProvideCommonCompositionLocals", "ProvideAndroidCompositionLocals",
    "ProvideContentColorTextStyle", "ProvideTextStyle", "Content", "BasicText", "BasicTextField",
})
"""Pass-through wrappers and private implementations: pure plumbing, dropped from the displayed call
path so the composables the app wrote stand out (`BasicText`/`BasicTextField` are the internals of
`Text`/`TextField`)."""


GENERIC_COMPOSABLES = frozenset({
    "AndroidView", "BasicText", "BasicTextField", "Box", "Column", "CompositionLocalProvider",
    "Divider", "FlowColumn", "FlowRow", "HorizontalDivider", "Icon", "Image", "Key", "LazyColumn",
    "LazyHorizontalGrid", "LazyRow", "LazyVerticalGrid", "ProvideContentScale", "ProvideTextStyle",
    "Row", "Scaffold", "Spacer", "Surface", "Text", "VerticalDivider",
})
"""Compose's own building blocks: their name repeats what the semantics already tell us, so a name
the app wrote itself is preferred when both drew the same box."""

MIN_NAME_OVERLAP = 0.8


def _fits_here(hits: list["ComposeHit"], bounds) -> list["ComposeHit"]:
    """The reported nodes that belong to this box: same bounds first, then the ones that cover it."""
    exact = [hit for hit in hits if hit.bounds == bounds]
    if exact:
        return sorted(exact, key=lambda hit: (hit.name in GENERIC_COMPOSABLES, len(hit.path),
                                              hit.name or ""))
    area = max(1, bounds.width * bounds.height)
    scored: list[tuple[float, "ComposeHit"]] = []
    for hit in hits:
        other = hit.bounds
        if other is None:
            continue
        width = min(bounds.right, other.right) - max(bounds.left, other.left)
        height = min(bounds.bottom, other.bottom) - max(bounds.top, other.top)
        if width <= 0 or height <= 0:
            continue
        cover = (width * height) / min(area, max(1, other.width * other.height))
        if cover >= MIN_NAME_OVERLAP:
            scored.append((cover, hit))
    scored.sort(key=lambda item: (-item[0], item[1].name in GENERIC_COMPOSABLES, len(item[1].path)))
    return [hit for _, hit in scored]


def apply_compose_names(root: ViewNode, hits: Iterable["ComposeHit"]) -> int:
    """Write the composable names the agent read inside the app onto the nodes of the snapshot.

    A layout node and the semantics node uiautomator reports for it share their box on the screen,
    so that is how the two are joined; `props["compose"]` then carries name, file, line and the call
    path. Returns how many nodes got a name.
    """
    named = [hit for hit in hits
             if hit.name and hit.bounds and hit.bounds.width > 0 and hit.bounds.height > 0]
    attached = 0
    for node in compose_nodes(root):
        if node.bounds is None or node.bounds.width <= 0 or node.bounds.height <= 0:
            continue
        matches = _fits_here(named, node.bounds)
        if not matches:
            continue
        best = matches[0]
        props = {"name": best.name}
        if best.file:
            props["file"] = best.file
        if best.line is not None:
            props["line"] = str(best.line)
        chain = _call_chain(best.path)
        if len(chain) > 1:
            props["path"] = " > ".join(chain)
        node.props["compose"] = props
        attached += 1
    return attached


def _call_chain(path: Iterable[str]) -> list[str]:
    """The composables the app wrote, in call order: the raw path with pure plumbing dropped and
    runs of the same name collapsed (`QuestionWithSelectionScreen > ScaffoldScreen > AdaptiveFitLayout
    > QuestionContent > Column > RadioGroup > RadioItem`)."""
    chain: list[str] = []
    for part in path:
        if part in STRUCTURAL_COMPOSABLES:
            continue
        if not chain or chain[-1] != part:
            chain.append(part)
    return chain


# --- building the real Compose tree -----------------------------------------------------------
#
# The agent gives, for every layout node, the composables it was called through *and a stable id per
# composable* (`ComposeHit.path_ids`): the one `RadioGroup` that every list item passes through has
# the same id in each item's path. That is what lets us show the tree the app actually wrote -- one
# `RadioGroup` with the items nested under it -- instead of repeating the whole path on each item.


def _id_chain(path: tuple[str, ...], path_ids: tuple[int, ...]) -> list[tuple[int, str]]:
    """The call path as (group id, composable) pairs, with the same plumbing dropped and repeats
    collapsed as `_call_chain`, keeping the first id of a collapsed run."""
    chain: list[tuple[int, str]] = []
    for name, gid in zip(path, path_ids):
        if name in STRUCTURAL_COMPOSABLES:
            continue
        if chain and chain[-1][1] == name:
            continue
        chain.append((gid, name))
    return chain


def _union(a: Rect | None, b: Rect | None) -> Rect | None:
    if a is None:
        return b
    if b is None:
        return a
    return Rect(min(a.left, b.left), min(a.top, b.top), max(a.right, b.right), max(a.bottom, b.bottom))


def _fold_bounds(node: ViewNode) -> Rect | None:
    """A composable's box is the union of its own layout nodes and everything nested under it."""
    bounds = node.bounds
    for child in node.children:
        bounds = _union(bounds, _fold_bounds(child))
    node.bounds = bounds
    return bounds


def _enrich(nodes: Iterable[ViewNode], semantics: list[ViewNode]) -> set[int]:
    """Copy what only uiautomator knows (text, content-desc, state) onto the composable that drew
    the same box, matched by exact bounds. Returns the semantics nodes that found a home."""
    by_bounds: dict[Rect, list[ViewNode]] = defaultdict(list)
    for node in semantics:
        if node.bounds is not None:
            by_bounds[node.bounds].append(node)
    used: set[int] = set()
    for node in nodes:
        if node.bounds is None:
            continue
        for semantic in by_bounds.get(node.bounds, ()):
            if id(semantic) in used:
                continue
            node.text = node.text or semantic.text
            ui = semantic.props.get("uiautomator")
            if ui is not None:
                node.props["uiautomator"] = dict(ui)
                if "uiautomator" not in node.sources:
                    node.sources.append("uiautomator")
            used.add(id(semantic))
            break
    return used


def _merge_duplicates(nodes: list[ViewNode]) -> list[ViewNode]:
    """Collapse siblings that are the same composable drawn at the same box: Compose composes some
    content twice -- a lookahead/measure pass and the real one (`AnimatedContent`, shared-element
    transitions) -- so the slot table holds two groups, same name and bounds but different ids, one
    of them without placed semantics. Same name + same bounds means the same thing, so they merge
    (children pooled, then deduped one level down), keeping whichever pass carries text/semantics."""
    merged: list[ViewNode] = []
    seen: dict[tuple[str | None, object], ViewNode] = {}
    for node in nodes:
        key = (node.props.get("compose", {}).get("name"), node.bounds)
        target = seen.get(key) if key[0] is not None and node.bounds is not None else None
        if target is None:
            seen[key] = node
            merged.append(node)
            continue
        target.children.extend(node.children)
        if not target.text and node.text:
            target.text = node.text
        if "uiautomator" not in target.props and "uiautomator" in node.props:
            target.props["uiautomator"] = node.props["uiautomator"]
            if "uiautomator" not in target.sources:
                target.sources.append("uiautomator")
    for node in merged:
        node.children = _merge_duplicates(node.children)
    return merged


def build_compose_subtree(hits: Iterable["ComposeHit"],
                          semantics: list[ViewNode] | None = None) -> list[ViewNode]:
    """The composable tree a ComposeView drew, from the agent's nodes: composables interned by their
    group id so a shared parent is one node, with the layout nodes nested underneath. uiautomator is
    used only to enrich the matching leaves (text, state) -- the hierarchy itself is compose's. A
    composable composed twice (a lookahead pass) is merged back to one node."""
    roots: list[ViewNode] = []
    by_ids: dict[tuple[int, ...], ViewNode] = {}
    for hit in hits:
        if hit.bounds is None or hit.bounds.width <= 0 or hit.bounds.height <= 0:
            continue
        chain = _id_chain(hit.path, hit.path_ids)
        if not chain:
            continue
        siblings, prefix, node = roots, (), None
        for gid, name in chain:
            prefix += (gid,)
            node = by_ids.get(prefix)
            if node is None:
                node = ViewNode(class_name=f"androidx.compose.{name}", sources=["compose"],
                                props={"compose": {"name": name, "id": str(gid),
                                                   "path": " > ".join(n for _, n in chain)}})
                by_ids[prefix] = node
                siblings.append(node)
            siblings = node.children
        assert node is not None  # chain is non-empty
        node.bounds = _union(node.bounds, hit.bounds)
        compose = node.props["compose"]
        if hit.file and "file" not in compose:
            compose["file"] = hit.file
        if hit.line is not None and "line" not in compose:
            compose["line"] = str(hit.line)
    for root in roots:
        _fold_bounds(root)
    # deepest first: when a leaf and its folded-up ancestor share a box, the leaf is the real element
    _enrich(reversed(list(by_ids.values())), semantics or [])
    roots = _merge_duplicates(roots)
    _mark_ghosts(roots)
    return roots


def _backed(node: ViewNode) -> int:
    """How many nodes under here (self included) matched an on-screen uiautomator semantics node."""
    return sum(1 for n, _ in node.walk() if "uiautomator" in n.sources)


def _mark_ghosts(siblings: list[ViewNode]) -> None:
    """Flag a duplicate pass that is not on screen. Compose can keep a composable composed twice (a
    lookahead measure pass, or an `AnimatedContent` state being animated out): the copies have the
    same name, but only the real one's leaves match uiautomator. So among same-named siblings, if one
    has on-screen leaves and another has none, the empty one is a ghost -- whereas a composable that
    simply draws no semantics (a custom radio box) is the only one of its name and is left alone."""
    by_name: dict[str, list[ViewNode]] = defaultdict(list)
    for node in siblings:
        name = node.props.get("compose", {}).get("name")
        if name:
            by_name[name].append(node)
    for group in by_name.values():
        if len(group) < 2:
            continue
        counts = [(_backed(node), node) for node in group]
        if any(c > 0 for c, _ in counts) and any(c == 0 for c, _ in counts):
            for count, node in counts:
                if count == 0:
                    node.props["compose"]["ghost"] = "true"
    for node in siblings:
        _mark_ghosts(node.children)


def _is_interop(node: ViewNode) -> bool:
    """A real Android View hosted by Compose (AndroidView) is in the dumpsys tree; a pure Compose
    semantics node is uiautomator-only. Keep the former, replace the latter with the compose tree."""
    return any("dumpsys" in n.props for n, _ in node.walk())


def _in_host(hit: "ComposeHit", host: ViewNode) -> bool:
    if host.bounds is None or hit.bounds is None:
        return True
    cx = (hit.bounds.left + hit.bounds.right) // 2
    cy = (hit.bounds.top + hit.bounds.bottom) // 2
    return host.bounds.left <= cx <= host.bounds.right and host.bounds.top <= cy <= host.bounds.bottom


def _prune_ghosts(nodes: list[ViewNode]) -> list[ViewNode]:
    """Drop the duplicate passes `_mark_ghosts` flagged (whole subtree with them), keep the rest."""
    kept = []
    for node in nodes:
        if node.props.get("compose", {}).get("ghost") == "true":
            continue
        node.children = _prune_ghosts(node.children)
        kept.append(node)
    return kept


def graft_compose_tree(root: ViewNode, hits: Iterable["ComposeHit"],
                       show_ghosts: bool = False) -> int | None:
    """Replace the flat semantics nodes under every Compose host with the real composable tree the
    agent reported. Returns how many composable nodes were placed, or None when the dump carries no
    group ids (an older agent) so the caller falls back to the name overlay. Ghost passes (a
    composable composed twice, the copy not on screen) are dropped unless `show_ghosts`."""
    hits = list(hits)
    if not any(hit.path_ids for hit in hits):
        return None
    placed = 0
    for node, _ in list(root.walk()):
        if not node.class_name.endswith("AndroidComposeView"):
            continue
        semantics: list[ViewNode] = []
        kept: list[ViewNode] = []
        for child in node.children:
            if _is_interop(child):
                kept.append(child)
            else:
                semantics.extend(n for n, _ in child.walk())
        here = [hit for hit in hits if _in_host(hit, node)]
        subtree = build_compose_subtree(here, semantics)
        if not show_ghosts:
            subtree = _prune_ghosts(subtree)
        node.children = subtree + kept
        placed += sum(1 for n in subtree for sub, _ in n.walk() if "compose" in sub.props)
    return placed
