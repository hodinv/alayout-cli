# Real composable names: in-process agent — work in progress

Status: **WORKING end-to-end on a real device — real composable names are extracted and labelled.**
Nothing is committed. `python -m pytest` → 234 passed.

## Fix 2026-10-05 (c) — node->name link lost when source info is on (names recorded but unlabelled)

A question screen showed only `AnimatedContent`. The dump proved names *were* recorded
(`withSourceInfo:391`, paths held `QuestionWithSelectionScreen > ScaffoldScreen > Scaffold > ...`),
but `named:4` / `withNode:4` — almost no on-screen node was linked to a name, so nodes fell back to
overlap-matching the one big full-screen hit that did link (`AnimatedContent`). Cause (SlotTable.kt):
once collection is on, each group is surfaced as a `SourceInformationSlotTableGroup` whose `node`
getter is always null, so `group.getNode()` in `walkGroups` returned null for nearly every node.
First attempt (`nodeOf` scanning `group.getData()`) failed: the LayoutNode is a dedicated node slot,
not in the group's data slots, so `named` stayed 4. Real fix: walk the slot table **by group index**
(`ComposeDump.indexWalk`) using `SlotTable.getGroupsSize()`, `isNode(i)`, `node(i)`,
`sourceInformationOf(i)` and `parentOf(i)` (via new `Ref.callInt`) -- these return the node and its
source string straight from an index, where the tooling `getCompositionGroups()` iterator nulls the
node for every source-info group. Per node group the path is the function names from the root down
(walk `parentOf`). The old iterator `walkGroups` stays only as a fallback when a table isn't a raw SlotTable.

Second attempt used `SlotTable.isNode/node/parentOf(i)` — but a device debug dump proved those are NOT
reflectable instance methods on the real runtime (they compile to inline/`int[]` helpers); only
`getGroups`, `getGroupsSize`, `slotsOf`, `sourceInformationOf` are. So that regressed to `named:0`.
Final `indexWalk`: the node is found by scanning `slotsOf(i)` for `androidx.compose.ui.node.LayoutNode`
(the node is among its group's slots), source string from `sourceInformationOf(i)`, and the parent
index read straight from the `getGroups()` int array (5 ints/group, parent at offset 2 — stable
1.3..1.7). Uses only confirmed-present methods.

Then a last gap: `indexWalk` named the 4 main-table nodes (deep paths to `Scaffold > ScaffoldLayout >
SubcomposeLayout`) but `named` stayed 4 -- the content rows live in the Scaffold's SubcomposeLayout
*subcomposition* tables, and `walkNodes` only called `collectSubcomposition(node)` for UNNAMED nodes,
so the now-named SubcomposeLayout host blocked the descent. Fix: `walkNodes` always calls
`collectSubcomposition` (a node can be both named and host a subcomposition).

Still `named:4`: `collectSubcomposition` walked only the FIRST table (`compositionData` = tables[0]).
A LazyColumn has one subcomposition (worked), but a Scaffold's SubcomposeLayout has a slot per top
bar / content / bottom bar, so the content table was missed (this also failed in the old code). Fix:
walk ALL tables the host's state leads to (`compositionTables` + walk each).

Then `named:11` (TopBar slot appeared, content still missing): `composeObjects` walked object FIELDS
only, never into collections -- and subcompositions are held in a `Map<LayoutNode, Composition>`, so
a directly-referenced slot (TopBar) was reachable but the content slot's table was not. Fix:
`composeObjects` now also descends into any Collection/Map/array element (via `Ref.items`), and its
visit cap was raised 3000 -> 20000. The per-node `path` already carries the full `@Composable fun`
chain (e.g. QuestionWithSelectionScreen > ScaffoldScreen > Scaffold > ...); these are the compiler's
`C(FunctionName)` tags. With coverage fixed, `named` jumped to 121. Two more pieces (done):
- **Cross-boundary paths.** A node's path only covered its own subcomposition's slot table, so a
  footer item read `QuestionFooter > ...` not the full chain. The host node that owns a subcomposition
  is already named with its full path, so `collectSubcomposition` now passes it as a prefix
  (`walkTable`/`indexWalk` take a `prefix`), joining the chain across composition boundaries ->
  `QuestionWithSelectionScreen > ScaffoldScreen > AdaptiveFitLayout > QuestionContent > ... > RadioItem`.
- **Show the whole chain.** `compose._call_chain` drops pure plumbing (`STRUCTURAL_COMPOSABLES`, now
  shared with `agent._compose_name`) and collapses repeats; stored in `props["compose"]["path"]` and
  shown as the tree label (tail-clipped to 48 so the most specific composable stays visible), full
  chain in the node detail panel. These names are the compiler's `C(FunctionName)` tags = the app's
  `@Composable fun`s. Tests: `test_compose` (call chain), `test_format` (chain label).

Then: showing the whole chain on every row made a shared parent repeat on each child (all RadioItems
read "...RadioGroup"), so the label was reverted to the node's OWN composable (chain stays in the
detail panel). And a composable that renders items inline gives each child the same nearest name
(RadioGroup), so `compose._distinguish_repeats` renames a child that duplicates its nearest named
ancestor after what is unique to it (the chain past the shared prefix): the RadioGroup names once,
its children become RadioItem/Row. Host-side only (compose.py/format.py) -- no agent rebuild. 239 pass.

## Fix 2026-10-05 (b) — hot reload must run BEFORE the user navigates

Symptom: on a single-activity NavHost app the capture always showed the start screen (a blink, then
start). Cause: `simulateHotReload` tears down and rebuilds the root composition, resetting the
NavController back stack to the start destination -- and it ran at *capture* time, after the user
navigated. Fix: split the agent protocol into `prepare` (enable collection + one hot reload) and
`dump` (read only). The host sends `prepare` right after launch, before prompting; the user then
navigates and that screen records its source info on insert (collection is already on); `dump` just
reads the live tree. So the blink now happens on the start screen, before navigation. Agent keeps the
prepared `ComposeDump` and reuses it for `dump`; a bare `dump` still self-prepares (back-compat).
`ComposeDump.ackJson` answers `prepare`; host `_prepare()` retries a few times (app may not have
composed right after launch); request seqs now come from a counter so repeats are distinct lines.

## RESOLVED 2026-10-05 — names appear

4th device dump on com.quitsmoke.tracker (Compose UI 1.7): `debug.hotReloaded:true`,
`debug.groups` `withSourceInfo:72` (was 0), `named:86`; nodes carry real `path`s and `sourceInfo`
(`C89@…:MainScreen.kt#…`). Real app composables come through: MainScreen, Scaffold, NavHost,
MoneyCard, LungFunctionCard, ImmuneSystemCard, CoughReliefCard, NavigationBar, NavigationBarItem,
ProgressCircle, Text/Image/Box/Row/Column. The fix was: enable `collectParameterInformation()` on
every live composer, then **hot reload** (`HotReloaderKt.simulateHotReload`) to re-insert the tree
so source info is filed on insert (see the 3rd-dump note below for why recompose never sufficed).

**Headline label (`parse_agent_dump._compose_name`, user's choice):** the node's own group is
positional, so the name is taken from the call `path` — plumbing wrappers dropped, then (unless the
nearest composable is a text primitive) generic building blocks are climbed past to the app/Material
component: MoneyCard's box reads `Card`, a ProgressCircle's Image reads `ProgressCircle`, a label
reads `Text`. Full path kept in `props["compose"]["path"]`. Tests: `tests/test_agent.py`,
`tests/test_compose.py`, fixture `tests/fixtures/compose_agent.json` (trimmed real dump).

## Update 2026-10-05 — bounded wiring done, name extraction still the one open item

Host-side plumbing agreed with the user and finished (tests added, suite green). The blocking
device item (`sourceInfo` empty) is unchanged and is the next task, to be iterated on the phone.

- **Flag renamed `--names` → `--compose`.** The agent flow runs only when `--compose` is passed
  (`cli.py`: `ComposeOpt`, `compose=`/`ctx.obj["compose"]` throughout; `--names` is gone).
- **Signed agent cached per package.** `agent_apk()` is now `cache/<package>/agent-<key>.apk`, with
  the build scratch dir alongside it; the generated alayout keystore stays shared at
  `cache/alayout.keystore`. `_needs_build()` still rebuilds only when the agent sources are newer,
  so a cached signed APK is reused and **never re-signed** on a repeat run.
- **Explicit signing config.** `SigningKey` now splits `store_password`/`key_password` (apksigner
  `--ks-pass`/`--key-pass`, keytool `-storepass`/`-keypass`). New CLI flags `--keystore`,
  `--key-alias`, `--key-password`, `--key-store-password` (on the global callback + `capture` +
  `inspect`) build a `signing_override()` that is threaded `collect(..., signing=) → _attach →
  signing_keys(override=)` and is then the only key tried. Env `ALAYOUT_KEYSTORE` still works and
  gained `ALAYOUT_KEY_STORE_PASSWORD` (falls back to `ALAYOUT_KEY_PASSWORD`).
- **Tests:** new `tests/test_agentbuild.py` (per-package path, override precedence, password split,
  cache reuse does not re-sign, full fake build) + `tests/test_cli.py` (`--compose`, `--keystore`
  override). `tests/test_minor_fixes.py` updated to `--compose`.

- **`ALAYOUT_DEBUG=1` wired** → `collect(debug=True)`, so `ALAYOUT_DEBUG=1 alayout --compose capture`
  puts the agent's `debug` object (group counters, `enabledBy`, `nodeToString`) into the snapshot's
  `raw/compose.json`. This is the diagnostic feed for the loop below (old Next steps §4, done).

### Diagnosis of `withSourceInfo: 0` (from a real `-e debug true` dump, Compose UI 1.7, com.quitsmoke.tracker)

The debug dump showed `tooling:true`, `SlotTable.sourceInformationMap` a live (empty) `HashMap`, every
group `getSourceInfo()==null`, `enabledBy:""`. Traced through the 1.7.6 runtime/ui sources:

- `Composer.collectParameterInformation()` (Composer.kt) sets `forceRecomposeScopes=true`,
  `sourceMarkersEnabled=true` and calls `slotTable.collectSourceInformation()` -- which only *creates*
  an empty `sourceInformationMap`. The map is filled only when a composition **re-emits** its groups
  while collecting. So enabling worked; nothing recomposed afterwards -> map stays empty -> every
  `SlotTableGroup.sourceInfo` (SlotTable.kt ~3532, reads `table.sourceInformationOf(group)`) is null.
- `enabledBy:""` was a red herring: `enable()` ran before `dump()` set `this.debug`. Now `prepare()`
  takes `debug` and sets it first, and `enabledCount`/`reflowed` counters were added.
- `reflow()` was the intended recompose trigger but a **no-op**: `LocalDensity` is a *static*
  `compositionLocalOf` provided from `AndroidComposeView.density` (CompositionLocals.kt 101/221), so
  writing a new `Density` into that state recomposes the whole content -- but the old code did
  `newInstance("androidx.compose.ui.unit.Density", ...)` (an **interface** -> null) and wrote an
  **equal** value (dropped by `MutableState`).

**Fix applied (agent side, needs device verification):** `Ref.staticCall()` added; `reflow()` now calls
the public factory `androidx.compose.ui.unit.DensityKt.Density(density, fontScale)` keeping the real
`density` (bounds stay exact) and nudging `fontScale` by 0.0001 so the value is unequal -> the static
`LocalDensity` recomposes the whole subtree, and with tooling now on the groups are filed with source
information. `prepare(instrumentation, debug)` sets debug early; debug now reports `enabledCount`,
`reflowed`, and a populated `enabledBy`. Rebuilds+resigns the agent on next run (sources changed).

**To verify:** re-run `ALAYOUT_DEBUG=1`; in `raw/compose.json` expect `debug.reflowed >= 1`,
`debug.enabledCount >= 1`, and `debug.groups` summary `withSourceInfo > 0`; top-level nodes should
carry `sourceInfo`/`path`. If `withSourceInfo` is still 0 with `reflowed>=1`, the recompose did not
re-emit through the enabled composer -> next lever is enabling collection on the root
CompositionContext *before* navigation (so new subcompositions inherit `sourceMarkersEnabled`).

### 2nd device dump: `reflowed:0` → `Ref.singleArg` didn't search superclasses

The fixed `reflow()` ran but `reflowed:0` while `enabledCount:8` / `enabledBy:"ComposerImpl ×8"`. Cause:
`Ref.singleArg` (used by `runWith`) scanned only `owner.getDeclaredMethods()`. The density state is a
`ParcelableSnapshotMutableState`, but `setValue(Object)` is declared on its superclass
`SnapshotMutableStateImpl`, so the write silently no-op'd. Fixed `singleArg` to walk the class
hierarchy (superclasses + interfaces), like `voidMethod`/`search`. This also strengthens the
`Recomposer.invalidate(composition)` call. Expect next run: `reflowed>=1` and `withSourceInfo>0`.

### 3rd dump: `reflowed:1` but still `withSourceInfo:0` → source info is filed only on INSERT

The density nudge worked (`reflowed:1`, tree re-measured) but no source info. Root cause in the 1.7.6
runtime: `ComposerImpl.sourceInformation` / `sourceInformationMarkerStart/End` all guard on
`if (inserting && sourceMarkersEnabled)` (Composer.kt ~3488). Source information is filed **only while
a group is inserted**, never while an existing group recomposes. So *any* recompose-the-live-tree
approach (invalidateAll, density nudge) is structurally incapable of filling the map. Also confirmed:
`Recomposer.collectingSourceInformation` is hardcoded `false`, and `LocalInspectionTables` only
registers the table, it does not enable markers.

**The fix that follows the runtime:** enable `collectParameterInformation()` on every live composer
(sets `sourceMarkersEnabled` on them), then run Compose's own hot reload
`HotReloaderKt.simulateHotReload(context)`. Hot reload's `HotReloadable.recompose()` does
`composition.setContent(composable)` — a real **re-insert through the same composer** — so the groups
are emitted with markers on and their source information is filed. Implemented: `Ref.staticRun`;
`enable()` now = enableTooling (all composers) → `simulateHotReload`; dropped the dead
`reflow()`/`invalidate()`; debug reports `hotReloaded`. Risk: simulateHotReload briefly clears+rebuilds
content (a flicker); the app is relaunched on finish anyway.

**To verify:** expect `debug.hotReloaded:true`, `debug.groups` `withSourceInfo>0`, and nodes carrying
`sourceInfo` like `C(Foo)…:File.kt#…`. If `hotReloaded:false`, simulateHotReload isn't on this version
→ fall back to `HotReloaderKt.invalidateGroupsWithKey(-1)` or a per-destination enable-before-navigate.

Next: the device loop for `sourceInfo` (see **Next steps** §1) — unchanged below.

## Goal

`uiautomator`/`dumpsys` cannot tell *which composable* drew a box: Compose puts no names into the
semantics tree, so alayout today only guesses roles (`⟨Button "OK"⟩`). The ask: inject code into a
**debuggable** app, restart the activity, let the user open the window they care about, and capture
the real names from inside the app.

## What was built

### Host side (Python) — done, needs tests
| File | What |
|---|---|
| `src/alayout/agentbuild.py` | finds JDK + SDK build-tools + `android.jar`, compiles the bundled Java agent (javac → d8), links a per-app manifest (aapt2), adds `classes.dex`, zipaligns, apksigner-signs. Caches APKs + keystore in `cache_dir()` (`ALAYOUT_CACHE` overrides). `signing_keys()` = keys to try; `build_agent(package, key=...)`. |
| `src/alayout/agent.py` | device flow: `foreground()`, `is_debuggable()`, `install_agent()`, `prepare()` (clean mailbox), `start()` = install + force-stop + `am instrument -w` in a thread + `am start`, `request()`/`read_dump()` (file mailbox via `run-as`), `finish()`, `collect(adb, wait, log, ...) -> Session`, `parse_agent_dump()`, `ComposeHit`/`AgentDump`. `SignatureRefused` → try the next key. |
| `src/alayout/compose.py` | **Preferred: `graft_compose_tree(root, hits)`** — rebuilds the real composable tree under each `AndroidComposeView` from the agent's `pathIds` (stable per-group ids): composables are interned by their id-prefix so a shared `RadioGroup` is **one** node with the items nested under it, internal nodes get the union of their leaves' bounds, interop `AndroidView`s are kept, and uiautomator semantics are folded onto the matching leaves by bounds. Returns `None` when the dump has no ids (older agent) → fall back to `apply_compose_names(root, hits)`, the flat bounds-overlay (exact first, then ≥80 % overlap, preferring a name the app wrote). `GENERIC_COMPOSABLES`/`STRUCTURAL_COMPOSABLES` classify names. |
| `src/alayout/build.py` | `_apply_names()`; `capabilities["compose"]` = `ok (N of M Compose views named)` or the reason it failed. |
| `capture.py` / `snapshot_io.py` | `RawCapture.compose_json`, kept in the snapshot as `raw/compose.json`. |
| `adb.py` | `Adb.install()`/`Adb.uninstall()`, shared `sdk_dirs()`. |
| `format.py` / `search.py` | the real name becomes the tree/wireframe label, `⟨role⟩` kept as a hint; names are searchable with `/`. |
| `cli.py` | `--names` on `alayout`, `capture`, `inspect` and as a global option; `_wait_for_screen()` prompts ("open the screen, press Enter"); an agent failure is a warning — the capture still happens. |

### Agent side (Java, bundled, pure reflection)
`src/alayout/resources/agent/`
- `AndroidManifest.xml` — `<instrumentation android:targetPackage="@TARGET_PACKAGE@">` (substituted
  per app; `am instrument` only injects into the package named there).
- `AlayoutAgent.java` — the Instrumentation. Starts from **`onCreate()` and `onStart()`** (OEM builds
  differ; guarded by `begun`). Polls a request file (`<seq> dump|exit`); on `dump`:
  `ComposeDump.prepare()` → `sleep(TOOLING_WAIT_MS=1200)` → `tooling.dump()` → writes JSON.
- `ComposeDump.java` — windows via `WindowManagerGlobal.mViews` (+ `ActivityThread.mActivities`
  fallback) → `AndroidComposeView` → `getRoot()` (`Owner`) → `getChildren()`; bounds via
  `outerCoordinator`/`getCoordinates()` + `getBoundsInWindow`/`boundsInWindow` (also the Kotlin
  **extension** form `LayoutCoordinatesKt.boundsInWindow`); name + `path` from the slot table.
  `-e debug true` adds a `debug` object (reflection inventory, group counters, `LayoutNode.toString()`
  samples) — that is how this was reverse-engineered and how it will be repaired.
- `Ref.java` — tolerant reflection: strips Kotlin mangling (`name$module`, `name-hash`), matches
  zero-arg / one-arg / void / static-extension methods, `run`/`runWith`/`runInt`, cached lookups.

Answer JSON: `{agent, request, package, tooling, windows:[{root, activity, left, top,
composeViews:[{view, bounds, groups, nodes:[{bounds, rect, sourceInfo, path, pathIds}], debug?}]}], errors:[]}`

**Subcomposition stitching** (`Scaffold`, `LazyColumn`, …): `collectNames` walks **only the main
composition table** (per holder: `compositionData`), never every reachable table. A `SubcomposeLayout`
builds its slots (content, bars, list items) in separate tables; those are walked only by
`collectSubcomposition`, which prefixes each with the full path+ids of the host `SubcomposeLayout`
node. Walking all tables up front (the old behaviour) named the subcomposition groups first with a
short path and the `walked` guard then blocked the prefixed walk — so `QuestionContent` hung beside
`Scaffold` instead of under it, and the prefix code was effectively dead. The node walk reaches every
on-screen subcomposition through its host node, so nothing emitted is lost.

**`pathIds`** (added for the nested tree): one stable id per `path` element, `base + groupIndex`
where every slot table gets a globally-unique `base` per ComposeView. So the one `RadioGroup` that
every list item is composed through has the same id in each item's path, and the host nests the
items under a single node instead of repeating the path on each (see `ComposeDump.pathOf`/`Chain`
and `graft_compose_tree`). The tree label is now just the composable's own name; the full call path
stays in the detail rows. Ids line up one-to-one with names or are dropped on the host.

## Verified on the device (Xiaomi vayu, Android 13 / API 33, debug build of `com.quitsmoke.tracker`)

* Agent APK builds: javac → d8 → aapt2 link → add `classes.dex` → zipalign → apksigner (29 KB,
  signed); `build_agent()` works, cache reuse works.
* Instrumentation attaches, the agent runs, **the JSON round-trip works**: 38–40 KB answers written
  by the app and read back with `run-as`.
* Compose tree and bounds are real: `hits=126`, e.g. `[0,91][1080,247]`, `[48,121][456,217]`; debug
  samples show `RootMeasurePolicy`, `BoxMeasurePolicy`, `AnimatedEnterExitMeasurePolicy`,
  `LayoutNodeSubcompositionsState$createMeasurePolicy$1` — the actual nodes on screen.
* **Not working: `sourceInfo` is empty for every group** (`withSourceInfo: 0`), so names are still
  missing. `tooling=True` means `collectParameterInformation` was found and called.

## Findings (each one cost time — do not rediscover)

1. **`adb exec-out` swallows the remote exit code.** A `SecurityException` from `am instrument`
   looked like success. → the answer text is checked instead (`Attachment.check`).
2. **Signing**: this device refuses instrumentation unless agent and app share a signing key; the
   AOSP "target is debuggable" exception is not honoured. Debug builds are signed with
   `~/.android/debug.keystore`, so that key is tried first, then alayout's own key; `KEY_HINT`
   explains `ALAYOUT_KEYSTORE` / `ALAYOUT_KEY_ALIAS` / `ALAYOUT_KEY_PASSWORD`. Re-signing makes
   `adb install -r` fail with `INSTALL_FAILED_UPDATE_INCOMPATIBLE` → `install_agent` uninstalls
   `com.alayout.agent` and retries.
3. **`am instrument` without `-w` never calls `onStart()`** here (the process starts, `onCreate`
   runs, `onStart` doesn't). With `-w` it blocks until the agent finishes → `Attachment` runs it in
   a daemon thread and notices fast refusals with `check()`.
4. **Apps cannot write to `/data/local/tmp`** (SELinux `EACCES`, even debuggable ones) → the mailbox
   is `/data/data/<pkg>/cache/alayout_request` / `alayout_compose.json`, reached with
   `run-as <pkg> sh -c '…'` (works because the app is debuggable).
5. **A stale mailbox kills the session**: a leftover `"<n> exit"` makes the freshly attached agent
   `finish()` at once (it looked like "the agent never answered"). `prepare()` deletes both files
   *before* attaching.
6. **`Composer.getCompositionData` is gone in Compose 1.7.x**: reach the `SlotTable` by walking the
   Compose object graph (`ComposeView.composition: WrappedComposition` → `CompositionImpl` →
   `SlotTable`), BFS over `androidx.compose.*` field values, predicate = answers
   `getCompositionGroups`. Works: 220 groups, 35 with a node.
7. **Where the names are gated**: `SlotTableGroup.sourceInfo` only has content while the runtime is
   *recording* (`SlotTable.collectingSourceInformation == sourceInformationMap != null`). The
   `C(AnswerOption)89@2L40:Shop.kt#hash` strings exist in the DEX (our `composables` command reads
   them), but the composer files them in only after collection was switched on **and** the groups are
   emitted again. Hence: switch collection on, then make the composition run once more — which is
   why the flow restarts the app after attaching.
8. Android Studio "Apply Changes"/instant-run leaves `code_cache/.overlay/*.dex` and
   `instruments-*.jar` in the app; harmless here but it makes logcat noisy.
9. `logcat -s alayout` is the agent's only diagnostic channel; `_no_answer()` already appends its
   last lines to the error message.

## Next steps

1. **Make the names appear** (the only blocking item). Cheapest first:
   a. attach → `prepare()` (collection on) → **restart the activity** (`am start` again, or
      `Activity.recreate()` from the agent) → prompt the user → dump: the composition is then created
      while recording.
   b. or force one recomposition without a restart: after `collectParameterInformation`, write the
      current values back into `AndroidComposeView`'s density/layoutDirection state — any write
      invalidates every `LocalDensity` reader, the tree recomposes with the same layout — wait a
      frame, then dump. (`reflow()` in ComposeDump is a first attempt at exactly this.)
   c. if a Compose version keeps nothing, say so plainly — already implemented:
      `capabilities["compose"]` carries the agent's `errors`.
   Probe with `-e debug true` (`collect(..., debug=True)`) and read `debug.groups` /
   `debug.enabledBy` / `debug.nodeToString`.
2. **Tests** (none for the new code yet): `test_agentbuild.py` (tool discovery with a fake env,
   `build_agent` with an injected runner, cache reuse, manifest substitution, `signing_keys`),
   `test_agent.py` (`FakeAdb` with `install`/`uninstall` in `tests/helpers.py`, responses for
   `run-as`/`am instrument`, `parse_agent_dump` on a `tests/fixtures/compose_agent.json`,
   SignatureRefused → second key, timeout message), `test_compose.py` (`apply_compose_names`: exact,
   overlap, generic-name preference), `test_build.py`/`test_cli.py` (`--names` happy path + the
   failure is a warning, not fatal), `test_format.py` (named label).
3. **Packaging**: `scripts/build_binary.py` needs `--collect-data alayout` (or `--add-data
   src/alayout/resources`) so the frozen binary ships the agent sources; check `scripts/check_release.py`.
4. **CLI polish**: wire `AAYOUT_DEBUG=1` → `collect(debug=True)`; maybe `--agent-timeout`; README
   section for `--names` (needs a JDK + SDK build-tools + debuggable app + the app's signing key; the
   app gets restarted; `adb uninstall com.alayout.agent` cleans up; agent stays cached in
   `~/.cache/alayout`).
5. Decide on the leftovers: auto-`adb uninstall` of the agent, and whether `finish()` should relaunch
   the activity (it does now).

## Repro / scratch (outside the repo)

* `python C:\Temp\try_agent.py` — drives `agent.collect()` against the connected phone: launches the
  app with `monkey`, waits, dumps 4×, prints per-dump counters, writes `C:\Temp\dump*.json`, finishes.
* `ALAYOUT_CACHE=C:\Temp\ag\cache` was used while iterating so the real cache stayed clean.
* Sources read while reverse-engineering: `C:\Temp\src\{ui,runtime}-{1.6.8,1.7.6}-sources`,
  `C:\Temp\ax\*` (androidx-main `LayoutNode.kt`, `AndroidComposeView.kt`, `CompositionData.kt`,
  `SourceInformation.kt`), `C:\Temp\app\base.apk` (pulled from the device).
* The phone still has `com.alayout.agent` installed (the agent for `com.quitsmoke.tracker`, signed
  with the local debug key) → `adb uninstall com.alayout.agent`.

## Files touched

```
 M src/alayout/adb.py build.py capture.py cli.py compose.py format.py search.py snapshot_io.py
 M tests/test_minor_fixes.py            (the _capture monkeypatch now takes names=)
?? src/alayout/agent.py
?? src/alayout/agentbuild.py
?? src/alayout/resources/agent/AndroidManifest.xml
?? src/alayout/resources/agent/src/com/alayout/agent/{AlayoutAgent,ComposeDump,Ref}.java
?? docs/compose-agent-notes.md          (this file)
```
