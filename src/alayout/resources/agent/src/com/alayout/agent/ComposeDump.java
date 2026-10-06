package com.alayout.agent;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.ContextWrapper;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewParent;

import org.json.JSONArray;
import org.json.JSONObject;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The app's own Compose trees: every layout node with its screen bounds and the composable
 * function that produced it.
 *
 * Compose keeps no names in the semantics tree, but a debug build keeps the slot table of every
 * composition, and each group there records the source information the Compose compiler wrote
 * (`C(AnswerOption)89@2L40:QuestionWithSelectionScreen.kt#hash`) together with the node it emitted.
 * Walking the slot table once gives a node -> composable map, walking the layout tree gives the
 * bounds, and the two are joined here. Nothing is linked at compile time, so an app built against
 * another Compose version is read through the same name-tolerant look-ups (see Ref).
 */
public final class ComposeDump {

    private static final int MAX_NODES = 6000;
    private static final int MAX_GROUPS = 300000;
    private static final int MAX_DEPTH = 400;
    /** Where Compose's extension functions end up: `fun LayoutCoordinates.boundsInWindow()`. */
    private static final String LAYOUT_COORDINATES_KT = "androidx.compose.ui.layout.LayoutCoordinatesKt";

    private final List<String> errors = new ArrayList<String>();
    private final Map<Object, Group> named = new IdentityHashMap<Object, Group>();
    private final Set<Object> walked = Collections.newSetFromMap(new IdentityHashMap<Object, Boolean>());
    /** A globally-unique base per slot table, so `base + groupIndex` is a stable id for every group
      * of one ComposeView: the same `RadioGroup` every list item passes through gets the same id, so
      * the host can nest the items under one node instead of repeating the path on each. */
    private final Map<Object, Integer> tableBase = new IdentityHashMap<Object, Integer>();
    private int nextBase;
    private final List<View> seen = new ArrayList<View>();
    private final List<String> samples = new ArrayList<String>();
    private int groups;
    private int subTables;
    private int sourceInfoSeen;  // groups with source information in the last dump; 0 => names failed
    private boolean debug;
    private boolean tooling;
    private int enabledCount;
    private boolean hotReloaded;
    private final StringBuilder enabledBy = new StringBuilder();

    /** Switch the tooling collection on (see {@link #enable}) and remember what it said. */
    public static ComposeDump prepare(Instrumentation instrumentation, boolean debug) {
        ComposeDump dump = new ComposeDump();
        dump.debug = debug;  // before enable(), so enabledBy/counters are recorded while enabling
        dump.tooling = dump.enable(instrumentation);
        return dump;
    }

    /** A short answer to the `prepare` step: whether collection is on, without reading the tree. */
    public String ackJson(long request) {
        JSONObject json = new JSONObject();
        put(json, "agent", 1);
        put(json, "request", request);
        put(json, "prepared", tooling);
        put(json, "process", processName());
        put(json, "pid", android.os.Process.myPid());
        put(json, "hotReloaded", hotReloaded);
        put(json, "enabledCount", enabledCount);
        put(json, "errors", new JSONArray(errors));
        return json.toString();
    }

    /** A JSON document describing every window of this process that shows Compose. */
    public String dump(Instrumentation instrumentation, long request, boolean debug) {
        this.debug = debug;
        return run(instrumentation, request);
    }

    /** Make the app record the names and source lines of its composable calls, the same switch
      * Android Studio's Layout Inspector flips when it connects.
      *
      * A debug build keeps the strings (`C(AnswerOption)89@2L40:Shop.kt#hash`) but the runtime files
      * them into the slot table only while a group is *inserted* (`ComposerImpl.sourceInformation`
      * guards on `inserting && sourceMarkersEnabled`), never while it recomposes. So turning the
      * collection on is not enough on its own -- the tree that was already composed carries nothing.
      * We enable collection on every live composer, then run Compose's own hot reload, which disposes
      * and re-inserts every root composition *through those same composers*, so the groups are emitted
      * with `sourceMarkersEnabled` set and this time their source information is filed.
      */
    private boolean enable(Instrumentation instrumentation) {
        List<Object> hosts = new ArrayList<Object>();
        for (View top : topLevelViews(instrumentation)) {
            List<Object> found = composeViews(top);
            for (int i = 0; i < found.size(); i++) {
                hosts.addAll(holders((View) found.get(i)));
            }
        }
        boolean enabled = false;
        for (int i = 0; i < hosts.size(); i++) {
            enabled |= enableTooling(hosts.get(i));  // sourceMarkersEnabled on each composer
        }
        if (enabled) {
            hotReloaded = Ref.staticRun("androidx.compose.runtime.HotReloaderKt", "simulateHotReload",
                    (Object) instrumentation.getTargetContext());
            if (!hotReloaded) {
                errors.add("collection is on but the composition could not be re-inserted"
                        + " (no HotReloaderKt.simulateHotReload); composable names may be missing");
            }
        } else {
            errors.add("the composition would not start recording source information"
                    + " (no Composer.collectParameterInformation): this Compose version does not"
                    + " keep composable names for tools");
        }
        return enabled;
    }

    /** Ask the composer near this object to file the names and source lines of the calls it makes. */
    private boolean enableTooling(Object start) {
        boolean enabled = false;
        List<Object> found = composeObjects(start);
        for (int i = 0; i < found.size(); i++) {
            Object item = found.get(i);
            if (Ref.run(item, "collectParameterInformation")) {
                enabled = true;
                enabledCount++;
                if (debug && enabledBy.length() < 400) {
                    enabledBy.append(Ref.base(item.getClass().getSimpleName())).append(' ');
                }
            }
        }
        return enabled;
    }

    /** The Compose objects around this one: a composer, a composition or a slot table. */
    private List<Object> composeObjects(Object start) {
        Set<Object> visited = Collections.newSetFromMap(new IdentityHashMap<Object, Boolean>());
        List<Object> queue = new ArrayList<Object>();
        queue.add(start);
        for (int i = 0; i < queue.size() && visited.size() < 20000; i++) {
            Object item = queue.get(i);
            if (!visited.add(item)) {
                continue;
            }
            List<Object> values = Ref.values(item);
            for (int v = 0; v < values.size(); v++) {
                Object value = values.get(v);
                if (isCompose(value)) {
                    if (!visited.contains(value)) {
                        queue.add(value);
                    }
                } else {
                    // subcompositions are held in a Map<LayoutNode, Composition> and lists; the fields
                    // walk alone never reaches them, so descend into any collection/map/array too
                    List<Object> items = Ref.items(value);
                    for (int k = 0; k < items.size(); k++) {
                        Object element = items.get(k);
                        if (isCompose(element) && !visited.contains(element)) {
                            queue.add(element);
                        }
                    }
                }
            }
        }
        return new ArrayList<Object>(visited);
    }

    /** Groups that carried source information in the last dump: 0 means the compiler's names were
      * not filed into the slot table (collection did not take), so the caller can re-enable + retry. */
    public int sourceInfoSeen() {
        return sourceInfoSeen;
    }

    /** Re-enable tooling and hot-reload once more: a fallback when the first dump found no source
      * information (the composers for some embedded ComposeViews were not yet enabled at prepare). */
    public boolean refresh(Instrumentation instrumentation) {
        tooling = enable(instrumentation);
        return tooling;
    }

    private String run(Instrumentation instrumentation, long request) {
        sourceInfoSeen = 0;
        JSONObject json = new JSONObject();
        JSONArray windows = new JSONArray();
        List<View> tops = topLevelViews(instrumentation);
        for (int i = 0; i < tops.size(); i++) {
            JSONObject window = window(tops.get(i));
            if (window != null) {
                windows.put(window);
            }
        }
        if (windows.length() == 0) {
            errors.add("no androidx.compose.ui.platform.AndroidComposeView in any of the "
                    + tops.size() + " window(s) of " + where() + "; this screen is Views, or its "
                    + "activity runs in another process than the one am instrument attached to");
        }
        put(json, "agent", 1);
        put(json, "request", request);
        put(json, "package", instrumentation.getTargetContext().getPackageName());
        put(json, "process", processName());
        put(json, "pid", android.os.Process.myPid());
        put(json, "tooling", tooling);
        put(json, "windows", windows);
        put(json, "errors", new JSONArray(errors));
        return json.toString();
    }

    /** The process this agent is actually running in -- the one `am instrument` attached to. A
      * multi-process app may draw its UI in another process, which this agent cannot see. */
    private static String processName() {
        try {
            Object name = Ref.callStatic(Class.forName("android.app.ActivityThread"),
                    "currentProcessName");
            if (name instanceof String) {
                return (String) name;
            }
        } catch (Throwable ignored) {
            // older platform: fall back to the command line
        }
        try {
            java.io.BufferedReader reader = new java.io.BufferedReader(
                    new java.io.FileReader("/proc/self/cmdline"));
            try {
                String line = reader.readLine();
                if (line != null) {
                    return line.replace('\0', ' ').trim();
                }
            } finally {
                reader.close();
            }
        } catch (Throwable ignored) {
            // unreadable
        }
        return null;
    }

    private static String where() {
        return "process " + processName() + " (pid " + android.os.Process.myPid() + ")";
    }

    /** The windows of this process: what the WindowManager holds, with the activities as a
      * fallback, so a dialog or a popup window is captured as well as the screen behind it. */
    private List<View> topLevelViews(Instrumentation instrumentation) {
        List<View> out = new ArrayList<View>();
        seen.clear();  // one traversal at a time: prepare() and dump() each walk the windows
        try {
            Class<?> global = Class.forName("android.view.WindowManagerGlobal");
            Object instance = Ref.callStatic(global, "getInstance");
            addViews(out, Ref.items(Ref.field(instance, "mViews")));
        } catch (Throwable t) {
            errors.add("WindowManagerGlobal: " + t);
        }
        if (out.isEmpty()) {
            try {
                Class<?> thread = Class.forName("android.app.ActivityThread");
                Object current = Ref.callStatic(thread, "currentActivityThread");
                List<Object> records = Ref.items(Ref.field(current, "mActivities"));
                for (int i = 0; i < records.size(); i++) {
                    Object activity = Ref.field(records.get(i), "activity");
                    if (!(activity instanceof Activity)) {
                        continue;
                    }
                    Object window = Ref.call(activity, "getWindow");
                    addViews(out, Ref.items(Ref.call(window, "getDecorView")));
                }
            } catch (Throwable t) {
                errors.add("ActivityThread: " + t);
            }
        }
        if (out.isEmpty() && instrumentation != null) {
            errors.add("no windows found in " + where() + "; if the app's UI is on screen, its "
                    + "activity runs in a different process (a multi-process app) and am instrument "
                    + "attached to the wrong one");
        }
        return topMost(out);
    }

    /** Only the window the user is actually on. The process can hold several windows at once -- a
      * background activity whose composition is still alive sits behind the foreground one -- and
      * capturing them all mixes another screen's composables in. The foreground window is the one
      * with window focus; a focused popup/dialog carries its focus, so this keeps the right one.
      * If nothing reports focus (a brief transition), every window is kept rather than none. */
    private List<View> topMost(List<View> views) {
        List<View> focused = new ArrayList<View>();
        for (int i = 0; i < views.size(); i++) {
            try {
                if (views.get(i).hasWindowFocus()) {
                    focused.add(views.get(i));
                }
            } catch (Throwable ignored) {
                // a view not attached to a window: skip the focus test
            }
        }
        return focused.isEmpty() ? views : focused;
    }

    private void addViews(List<View> out, List<Object> views) {
        for (int i = 0; i < views.size(); i++) {
            Object view = views.get(i);
            if (view instanceof View && !contains(seen, view)) {
                seen.add((View) view);
                out.add((View) view);
            }
        }
    }

    private static boolean contains(List<View> views, Object view) {
        for (int i = 0; i < views.size(); i++) {
            if (views.get(i) == view) {
                return true;
            }
        }
        return false;
    }

    /** One window: where it starts on the screen and the Compose views it holds. */
    private JSONObject window(View top) {
        List<Object> hosts = composeViews(top);
        if (hosts.isEmpty()) {
            return null;
        }
        int[] location = new int[2];
        top.getLocationOnScreen(location);
        JSONObject json = new JSONObject();
        put(json, "root", top.getClass().getName());
        put(json, "activity", activityName(top));
        put(json, "left", location[0]);
        put(json, "top", location[1]);
        JSONArray views = new JSONArray();
        for (int i = 0; i < hosts.size(); i++) {
            JSONObject view = composeView((View) hosts.get(i), location[0], location[1]);
            if (view != null) {
                views.put(view);
            }
        }
        put(json, "composeViews", views);
        return views.length() == 0 ? null : json;
    }

    private List<Object> composeViews(View view) {
        List<Object> found = new ArrayList<Object>();
        collectComposeViews(view, found, 0);
        return found;
    }

    private void collectComposeViews(View view, List<Object> found, int depth) {
        if (view == null || depth > MAX_DEPTH || found.size() > 100) {
            return;
        }
        if (view.getClass().getName().endsWith("AndroidComposeView")) {
            found.add(view);
        }
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            for (int i = 0; i < group.getChildCount(); i++) {
                collectComposeViews(group.getChildAt(i), found, depth + 1);
            }
        }
    }

    /** The activity a window belongs to, or null for a window that is not an activity's. */
    private static String activityName(View view) {
        Context context = view.getContext();
        for (int i = 0; i < 12 && context != null; i++) {
            if (context instanceof Activity) {
                return context.getClass().getName();
            }
            if (!(context instanceof ContextWrapper)) {
                return null;
            }
            context = ((ContextWrapper) context).getBaseContext();
        }
        return null;
    }

    /** One ComposeView's content: its layout nodes with bounds and composable names. */
    private JSONObject composeView(View view, int windowLeft, int windowTop) {
        Object root = Ref.call(view, "getRoot");
        if (root == null) {
            root = Ref.field(view, "root");
        }
        if (root == null) {
            errors.add("no layout root in " + view.getClass().getName());
            return null;
        }
        int[] location = new int[2];
        view.getLocationOnScreen(location);
        named.clear();
        walked.clear();
        // tableBase / nextBase are NOT reset here: group ids stay unique across every ComposeView of
        // the dump, so the host can flatten all of them without one ComposeView's ids colliding with
        // another's (an app may have many ComposeViews -- 9 on this screen)
        samples.clear();
        groups = 0;
        collectNames(view);
        JSONArray nodes = new JSONArray();
        walkNodes(root, nodes, location, windowLeft, windowTop, new int[1], 0);
        JSONObject json = new JSONObject();
        put(json, "view", view.getClass().getName());
        put(json, "bounds", rect(location[0], location[1], view.getWidth(), view.getHeight()));
        put(json, "nodes", nodes);
        put(json, "groups", named.size());
        if (debug) {
            JSONObject info = new JSONObject();
            put(info, "view", reflect(view));
            put(info, "viewCompositions", reachable(view, 1));
            ViewParent parent = view.getParent();
            if (parent instanceof View) {
                put(info, "parent", reflect((View) parent));
                put(info, "parentCompositions", reachable((View) parent, 2));
            }
            put(info, "root", reflect(root));
            put(info, "nodeToString", new JSONArray(samples));
            put(info, "enabledBy", enabledBy.toString());
            put(info, "enabledCount", enabledCount);
            put(info, "hotReloaded", hotReloaded);
            Object coordinator = coordinatorOf(root);
            if (coordinator != null) {
                put(info, "coordinator", reflect(coordinator));
            }
            Object table = rootTable(view);  // the table collectNames actually walks
            put(info, "rootTable", table != null);
            if (table == null) {
                table = tableOf(view);
            }
            if (table != null) {
                put(info, "table", reflect(table));
                put(info, "groups", groups(table));  // its top groups: the app root, not a Scaffold sub
            }
            put(json, "debug", info);
        }
        if (named.isEmpty()) {
            errors.add("no composable names in " + view.getClass().getName()
                    + ": the composition kept no slot table (is the app a debuggable build?)");
        }
        return json;
    }

    /** The slot table of the composition shown by this view: node -> composable that made it.
      *
      * `AndroidComposeView` does not hold the composition itself -- the `ComposeView`/
      * `AbstractComposeView` above it does -- so the search goes up the view chain.
      *
      * Only the *main* composition is walked here, with no prefix. A `Scaffold`/`LazyColumn` builds
      * its slots (content, bars, list items) in *subcompositions* -- separate slot tables -- and those
      * are walked by {@link #collectSubcomposition} instead, which prefixes them with the full path of
      * the `SubcomposeLayout` node that hosts them. Walking every reachable table here (subcompositions
      * included) would name their groups first, with a short path, and the prefix walk -- blocked by
      * the `walked` guard -- would never run; so `QuestionContent` would hang beside `Scaffold`
      * instead of under it. The node walk reaches every on-screen subcomposition through its host node,
      * so nothing emitted is lost by leaving them to `collectSubcomposition`.
      */
    private void collectNames(View view) {
        Object root = rootTable(view);
        if (root != null) {
            walkTable(root, Collections.<String>emptyList(), Collections.<Integer>emptyList());
            return;
        }
        // no root composition we can reach directly: fall back to walking every reachable table with
        // no prefix -- nodes still get named, but a subcomposition's path is short (pre-fix behaviour)
        List<Object> holders = holders(view);
        List<Object> tables = new ArrayList<Object>();
        for (int i = 0; i < holders.size(); i++) {
            compositionTables(holders.get(i), tables);
        }
        if (tables.isEmpty()) {
            errors.add("no CompositionData reachable from " + view.getClass().getName());
            return;
        }
        for (int i = 0; i < tables.size(); i++) {
            walkTable(tables.get(i), Collections.<String>emptyList(), Collections.<Integer>emptyList());
        }
    }

    /** The view's own (root) composition slot table, reached by walking the composition object
      * directly -- `ComposeView.composition` (a `WrappedComposition`) -> `original` (`CompositionImpl`)
      * -> `slotTable` -- rather than by searching the object graph, whose first hit is often a
      * `Scaffold`/`LazyColumn` subcomposition. Returning the root (and only the root) is what lets
      * `collectSubcomposition` thread each subcomposition under its real host. */
    private Object rootTable(View view) {
        for (Object holder : holders(view)) {
            Object comp = Ref.field(holder, "composition");
            if (comp == null) {
                comp = Ref.call(holder, "getComposition");
            }
            for (int hop = 0; hop < 4 && comp != null; hop++) {
                Object slot = Ref.field(comp, "slotTable");
                if (isSlotTable(slot)) {
                    return slot;
                }
                Object data = Ref.call(comp, "getCompositionData", "getOriginalCompositionData");
                if (isSlotTable(data)) {
                    return data;
                }
                comp = Ref.field(comp, "original", "composition");  // WrappedComposition -> impl
            }
        }
        return null;
    }

    /** A raw `SlotTable` (walkable by group index), as opposed to a `CompositionData` wrapper. */
    private static boolean isSlotTable(Object table) {
        return table != null && Ref.call(table, "getGroupsSize") instanceof Integer;
    }

    /** Where a composition may live: the ComposeView and the AndroidComposeView it hosts. */
    private List<Object> holders(View view) {
        List<Object> out = new ArrayList<Object>();
        out.add(view);
        for (ViewParent parent = view.getParent();
             parent instanceof View && out.size() < 6; parent = ((View) parent).getParent()) {
            out.add(parent);
        }
        return out;
    }

    private Object tableOf(View view) {
        List<Object> holders = holders(view);
        for (int i = 0; i < holders.size(); i++) {
            Object data = compositionData(holders.get(i));
            if (data != null) {
                return data;
            }
        }
        return null;
    }

    /** `-e debug true`: what the slot table actually holds, so a Compose version that moved
      * `sourceInfo` or `node` shows up in the dump instead of silently naming nothing. */
    private JSONArray groups(Object table) {
        JSONArray out = new JSONArray();
        int[] counts = new int[4];  // groups, with source information, with a node, collected
        sample(table, out, counts, 0);
        JSONObject summary = new JSONObject();
        put(summary, "groups", counts[0]);
        put(summary, "withSourceInfo", counts[1]);
        put(summary, "withNode", counts[2]);
        put(summary, "named", named.size());
        out.put(summary);
        return out;
    }

    private void sample(Object group, JSONArray out, int[] counts, int depth) {
        if (depth > 200 || counts[0]++ > 20000) {
            return;
        }
        String info = Ref.text(Ref.call(group, "getSourceInfo"));
        Object node = nodeOf(group);
        if (info != null) {
            counts[1]++;
        }
        if (node != null) {
            counts[2]++;
        }
        if (out.length() < 20 && (info != null || node != null)) {
            JSONObject entry = new JSONObject();
            put(entry, "class", group.getClass().getName());
            put(entry, "sourceInfo", info);
            put(entry, "node", node == null ? null : node.getClass().getName());
            out.put(entry);
        }
        List<Object> children = Ref.items(Ref.call(group, "getCompositionGroups"));
        for (int i = 0; i < children.size(); i++) {
            sample(children.get(i), out, counts, depth + 1);
        }
    }

    /** Every slot table around this object: a screen is built by several compositions (the screen
      * itself, its subcompositions, dialogs), and the first one found is rarely the interesting one. */
    private void compositionTables(Object start, List<Object> into) {
        List<Object> found = composeObjects(start);
        for (int i = 0; i < found.size(); i++) {
            Object item = found.get(i);
            if (looksLikeData(item) && !into.contains(item)) {
                into.add(item);
            }
            Object data = Ref.call(item, "getCompositionData", "getOriginalCompositionData");
            if (looksLikeData(data) && !into.contains(data)) {
                into.add(data);
            }
        }
    }

    /** The slot table of the composition this object takes part in (the first one to answer). */
    private Object compositionData(Object start) {
        List<Object> tables = new ArrayList<Object>();
        compositionTables(start, tables);
        return tables.isEmpty() ? null : tables.get(0);
    }

    private static boolean isCompose(Object value) {
        String name = value.getClass().getName();
        return name.startsWith("androidx.compose.") || name.contains(".compose.");
    }

    /** What is one, two or three field hops away and knows about a composition: `-e debug true`
      * prints it, which is how a new Compose release is traced back to its slot table. */
    private JSONArray reachable(Object start, int depth) {
        JSONArray out = new JSONArray();
        List<Object> queue = new ArrayList<Object>();
        queue.add(start);
        int rounds = 0;
        while (!queue.isEmpty() && rounds++ < depth) {
            List<Object> next = new ArrayList<Object>();
            for (int i = 0; i < queue.size(); i++) {
                Object value = queue.get(i);
                String name = value.getClass().getName();
                if (isCompositionish(name) && out.length() < 60) {
                    out.put(name + (compositionData(value) != null ? " -> slot table" : ""));
                }
                next.addAll(Ref.values(value));
            }
            queue = next;
        }
        return out;
    }

    private static boolean isCompositionish(String name) {
        return name.contains("omposition") || name.contains("omposer") || name.contains("SlotTable")
                || name.contains("Recomposer");
    }

    private static boolean looksLikeData(Object data) {
        return data != null && Ref.call(data, "getCompositionGroups") != null;
    }

    /** Every group of the slot table, depth first: the source information of the group that
      * emitted a node, together with the composable functions it was called through. */
    private void walkGroups(Object group, List<String> path, List<Integer> ids, int depth) {
        if (groups++ > MAX_GROUPS || depth > MAX_DEPTH) {
            return;
        }
        List<Object> children = Ref.items(Ref.call(group, "getCompositionGroups"));
        for (int i = 0; i < children.size(); i++) {
            Object child = children.get(i);
            String info = Ref.text(Ref.call(child, "getSourceInfo"));
            if (info != null) {
                sourceInfoSeen++;
            }
            Object node = nodeOf(child);
            List<String> names = path;
            List<Integer> childIds = ids;
            String name = functionName(info);
            if (name != null) {
                names = new ArrayList<String>(path);
                names.add(name);
                childIds = new ArrayList<Integer>(ids);
                childIds.add(Integer.valueOf(System.identityHashCode(child)));  // best effort: no index
            }
            if (node != null && !named.containsKey(node)) {
                named.put(node, new Group(info, names, childIds));
            }
            walkGroups(child, names, childIds, depth + 1);
        }
    }

    /** The LayoutNode a group emitted.
      *
      * `CompositionGroup.getNode()` returns it for a plain group, but once source information is being
      * collected the same group is surfaced as a `SourceInformationSlotTableGroup` whose `node` is
      * always null (SlotTable.kt). The LayoutNode is still among the group's own slots, so it is
      * recovered from `getData()`; without this a screen whose groups all carry source information
      * loses almost every node -> name link, and the nodes fall back to the nearest big-box name.
      */
    private Object nodeOf(Object group) {
        Object node = Ref.call(group, "getNode");
        if (node != null) {
            return node;
        }
        List<Object> data = Ref.items(Ref.call(group, "getData"));
        for (int i = 0; i < data.size(); i++) {
            Object slot = data.get(i);
            if (slot != null && slot.getClass().getName().endsWith(".node.LayoutNode")) {
                return slot;
            }
        }
        return null;
    }

    /** A node built by a subcomposition (a LazyColumn row, a dialog's content) keeps its slots in
      * a table of its own, reachable through the state that subcomposes it. A single host can own
      * several subcompositions -- a Scaffold's SubcomposeLayout has a slot per top bar / content /
      * bottom bar -- so every table the state leads to is walked, not just the first. */
    private void collectSubcomposition(Object node) {
        if (subTables++ > 400) {
            return;
        }
        Object state = Ref.field(node, "subcompositionsState");
        if (state == null) {
            state = Ref.call(node, "getSubcompositionsState");
        }
        if (state == null) {
            return;
        }
        // the node hosting this subcomposition is already named with its full path; its content's
        // slot table only knows the path from the subcomposition root down, so the host's path is
        // carried in as a prefix to join the chain across the composition boundary
        Group host = named.get(node);
        List<String> prefix = host != null ? host.path : Collections.<String>emptyList();
        List<Integer> prefixIds = host != null ? host.pathIds : Collections.<Integer>emptyList();
        List<Object> tables = new ArrayList<Object>();
        compositionTables(state, tables);
        for (int i = 0; i < tables.size(); i++) {
            walkTable(tables.get(i), prefix, prefixIds);
        }
    }

    /** Walk a slot table once: several nodes of a list share the table that produced them. */
    private void walkTable(Object table, List<String> prefix, List<Integer> prefixIds) {
        if (!walked.add(table)) {
            return;
        }
        Object size = Ref.call(table, "getGroupsSize");
        if (size instanceof Integer) {
            indexWalk(table, ((Integer) size).intValue(), prefix, prefixIds);
        } else {  // not a raw SlotTable: best effort, with identity-hash ids
            walkGroups(table, new ArrayList<String>(prefix), new ArrayList<Integer>(prefixIds), 0);
        }
    }

    /** A stable id base for this table: `base + groupIndex` identifies each group within the view. */
    private int baseOf(Object table, int size) {
        Integer existing = tableBase.get(table);
        if (existing != null) {
            return existing.intValue();
        }
        int base = nextBase;
        nextBase += (size > 0 ? size : 1) + 1;
        tableBase.put(table, Integer.valueOf(base));
        return base;
    }

    /** The slot table's group records are 5 ints each; the parent group index is the 3rd field. These
      * have been stable across Compose 1.3..1.7. (`isNode`/`node`/`parentOf` exist in the sources but
      * compile to inline/`int[]` helpers that are not reflectable instance methods -- only
      * `getGroups`, `getGroupsSize`, `slotsOf` and `sourceInformationOf` are.) */
    private static final int GROUP_FIELDS = 5;
    private static final int PARENT_OFFSET = 2;
    /** Where Compose stores the LayoutNode a group emitted: among that group's own slots. */
    private static final String LAYOUT_NODE = ".node.LayoutNode";

    /** Walk the slot table by group index. The node a group emitted is among its slots (`slotsOf`),
      * and its source string comes from `sourceInformationOf` -- both keyed by index, where the
      * tooling `getCompositionGroups()` view returns a null node for every source-info group, so once
      * collection is on the iterator loses almost every node. The composable path is the function
      * names of the group's ancestors (parent index read straight from the `groups` int array). */
    private void indexWalk(Object table, int size, List<String> prefix, List<Integer> prefixIds) {
        Object raw = Ref.call(table, "getGroups");
        int[] groupArray = raw instanceof int[] ? (int[]) raw : null;
        int base = baseOf(table, size);
        for (int i = 0; i < size && groups < MAX_GROUPS; i++) {
            groups++;
            Object node = nodeInGroup(table, i);
            if (node == null || named.containsKey(node)) {
                continue;
            }
            Chain chain = pathOf(table, groupArray, i, base);
            List<String> path = new ArrayList<String>(prefix);
            path.addAll(chain.names);
            List<Integer> ids = new ArrayList<Integer>(prefixIds);
            ids.addAll(chain.ids);
            String info = sourceInfoAt(table, i);
            if (info != null) {
                sourceInfoSeen++;
            }
            named.put(node, new Group(info, path, ids));
        }
    }

    /** The LayoutNode stored in group `index`'s slots, or null if it is not a node group. */
    private Object nodeInGroup(Object table, int index) {
        List<Object> slots = Ref.items(Ref.callInt(table, index, "slotsOf"));
        for (int i = 0; i < slots.size(); i++) {
            Object slot = slots.get(i);
            if (slot != null && slot.getClass().getName().endsWith(LAYOUT_NODE)) {
                return slot;
            }
        }
        return null;
    }

    /** The composable names from the root down to group `index` (its own group included), each with
      * a stable id (`base + groupIndex`) so the host can tell when two nodes share an ancestor. */
    private Chain pathOf(Object table, int[] groupArray, int index, int base) {
        Chain up = new Chain();
        int current = index;
        for (int guard = 0; current >= 0 && guard < MAX_DEPTH; guard++) {
            String name = functionName(sourceInfoAt(table, current));
            if (name != null) {
                up.names.add(name);
                up.ids.add(Integer.valueOf(base + current));
            }
            int at = current * GROUP_FIELDS + PARENT_OFFSET;
            int parent = groupArray != null && at < groupArray.length ? groupArray[at] : -1;
            if (parent < 0 || parent >= current) {
                break;  // the root's parent is negative; a parent always precedes its children
            }
            current = parent;
        }
        Chain path = new Chain();
        for (int i = up.names.size() - 1; i >= 0; i--) {
            path.names.add(up.names.get(i));
            path.ids.add(up.ids.get(i));
        }
        return path;
    }

    /** The compiler's source string for a group (`C(MoneyCard)...`), from the side table. */
    private String sourceInfoAt(Object table, int index) {
        Object info = Ref.callInt(table, index, "sourceInformationOf");
        if (info == null) {
            return null;
        }
        Object text = Ref.call(info, "getSourceInformation");
        if (text == null) {
            text = Ref.field(info, "sourceInformation");
        }
        return Ref.text(text);
    }

    /** The literal string a text node draws. `BasicText` puts a `TextStringSimpleElement` (plain
      * String) or `TextAnnotatedStringElement` (an `AnnotatedString`, whose `text` is the String) on
      * its LayoutNode, so the real text is read straight off the node's modifiers -- no need for the
      * semantics tree, which Compose often merges away. Best effort: null when there is no text. */
    private String textOf(Object node) {
        List<Object> mods = Ref.items(Ref.call(node, "getModifierInfo"));
        for (int i = 0; i < mods.size(); i++) {
            Object modifier = Ref.call(mods.get(i), "getModifier");
            if (modifier == null) {
                continue;
            }
            String name = modifier.getClass().getName();
            if (!name.contains("Text") || !name.endsWith("Element")) {
                continue;
            }
            Object text = Ref.field(modifier, "text");
            if (text instanceof String) {
                return (String) text;
            }
            if (text != null) {  // an AnnotatedString: its own `text` field is the plain String
                Object plain = Ref.field(text, "text");
                if (plain instanceof String) {
                    return (String) plain;
                }
            }
        }
        return null;
    }

    private void walkNodes(Object node, JSONArray out, int[] viewLocation, int windowLeft,
                           int windowTop, int[] count, int depth) {
        if (node == null || count[0]++ > MAX_NODES || depth > MAX_DEPTH) {
            return;
        }
        // a node can be both named (it is in the main table) and host a subcomposition whose content
        // lives in a table of its own (a Scaffold slot, a LazyColumn item), so always try to descend
        collectSubcomposition(node);
        if (debug && samples.size() < 6) {
            samples.add(Ref.text(Ref.call(node, "toString")));
        }
        Placed placed = boundsOf(node, viewLocation, windowLeft, windowTop);
        Group group = named.get(node);
        if (placed != null || group != null) {
            JSONObject json = new JSONObject();
            if (placed != null) {
                put(json, "bounds", rect(round(placed.bounds[0]), round(placed.bounds[1]),
                        round(placed.bounds[2]), round(placed.bounds[3])));
                put(json, "rect", placed.kind);
            }
            if (group != null) {
                put(json, "sourceInfo", group.sourceInfo);
                put(json, "path", new JSONArray(group.path));
                put(json, "pathIds", new JSONArray(group.pathIds));
            }
            put(json, "text", textOf(node));
            out.put(json);
        }
        List<Object> children = childrenOf(node);
        for (int i = 0; i < children.size(); i++) {
            walkNodes(children.get(i), out, viewLocation, windowLeft, windowTop, count, depth + 1);
        }
    }

    /** What knows this node's place: `outerCoordinator` in 1.5..1.7, `getCoordinates()` elsewhere. */
    private Object coordinatorOf(Object node) {
        Object coordinator = Ref.field(node, "outerCoordinator", "coordinator", "coordinates");
        if (coordinator == null) {
            coordinator = Ref.call(node, "getOuterCoordinator", "getCoordinates");
        }
        return coordinator;
    }

    /** Absolute screen pixels: the node's bounds in its window plus where that window starts. */
    private Placed boundsOf(Object node, int[] viewLocation, int windowLeft, int windowTop) {
        Object coordinator = coordinatorOf(node);
        if (coordinator == null) {
            return null;
        }
        Object bounds = Ref.call(coordinator, "getBoundsInWindow", "boundsInWindow");
        String kind = "window";
        if (bounds == null) {
            bounds = Ref.extension(coordinator, LAYOUT_COORDINATES_KT, "boundsInWindow");
        }
        if (bounds == null) {
            bounds = Ref.call(coordinator, "getBoundsInRoot", "boundsInRoot", "getLocalBounds",
                    "localBounds");
            kind = "root";
        }
        if (bounds == null) {
            bounds = Ref.extension(coordinator, LAYOUT_COORDINATES_KT, "boundsInRoot");
            kind = "root";
        }
        if (bounds == null) {
            return null;
        }
        float left = Ref.number(bounds, "left");
        float top = Ref.number(bounds, "top");
        float right = Ref.number(bounds, "right");
        float bottom = Ref.number(bounds, "bottom");
        if (Float.isNaN(left) || Float.isNaN(top) || Float.isNaN(right) || Float.isNaN(bottom)) {
            return null;
        }
        int offsetX = "window".equals(kind) ? windowLeft : viewLocation[0];
        int offsetY = "window".equals(kind) ? windowTop : viewLocation[1];
        return new Placed(new float[] {left + offsetX, top + offsetY, right + offsetX,
                bottom + offsetY}, kind);
    }

    private List<Object> childrenOf(Object node) {
        Object kids = Ref.call(node, "getChildren");
        if (kids == null) {
            kids = Ref.field(node, "children", "_children");
        }
        List<Object> list = Ref.items(kids);
        if (list.isEmpty() && kids != null) {
            list = Ref.items(Ref.call(kids, "getContents", "asMutableList", "toList"));
        }
        if (list.isEmpty() && kids != null) {
            list = Ref.items(Ref.field(kids, "storage"));
        }
        return list;
    }

    /** The function name out of `C(AnswerOption)89@2L40:Shop.kt#hash` (CC marks an inlined call). */
    static String functionName(String info) {
        if (info == null || info.length() < 2 || info.charAt(0) != 'C') {
            return null;
        }
        int start = info.startsWith("CC") ? 2 : 1;
        if (start < info.length() && info.charAt(start) == '(') {
            int end = info.indexOf(')', start);
            if (end > start) {
                return info.substring(start + 1, end);
            }
        }
        return null;
    }

    private static int round(float value) {
        return (int) (value >= 0 ? value + 0.5f : value - 0.5f);
    }

    private static void put(JSONObject json, String key, Object value) {
        if (value == null) {
            return;
        }
        try {
            json.put(key, value);
        } catch (Throwable ignored) {
            // org.json only fails on NaN or a null key
        }
    }

    private static JSONArray rect(int left, int top, int right, int bottom) {
        JSONArray values = new JSONArray();
        values.put(left);
        values.put(top);
        values.put(right);
        values.put(bottom);
        return values;
    }

    private static final class Group {
        final String sourceInfo;
        final List<String> path;
        final List<Integer> pathIds;

        Group(String sourceInfo, List<String> path, List<Integer> pathIds) {
            this.sourceInfo = sourceInfo;
            this.path = path;
            this.pathIds = pathIds;
        }
    }

    /** A call path as two parallel lists: the composable names and their stable group ids. */
    private static final class Chain {
        final List<String> names = new ArrayList<String>();
        final List<Integer> ids = new ArrayList<Integer>();
    }

    private static final class Placed {
        final float[] bounds;
        final String kind;

        Placed(float[] bounds, String kind) {
            this.bounds = bounds;
            this.kind = kind;
        }
    }

    /** `-e debug true` reports what reflection can see, which is how this agent is repaired when a
      * new Compose version moves the fields and methods around. */
    private static JSONObject reflect(Object sample) {
        JSONObject json = new JSONObject();
        put(json, "class", sample.getClass().getName());
        JSONArray fields = new JSONArray();
        JSONArray methods = new JSONArray();
        for (Class<?> c = sample.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
            try {
                Field[] declared = c.getDeclaredFields();
                for (int i = 0; i < declared.length && i < 200; i++) {
                    Object value = null;
                    try {
                        declared[i].setAccessible(true);
                        value = declared[i].get(sample);
                    } catch (Throwable ignored) {
                        // unreadable
                    }
                    fields.put(Ref.base(declared[i].getName()) + ":"
                            + (value == null ? "-" : value.getClass().getSimpleName()));
                }
                Method[] all = c.getDeclaredMethods();
                for (int i = 0; i < all.length && methods.length() < 400; i++) {
                    if (useful(all[i].getName())) {
                        methods.put(Ref.base(all[i].getName()) + "/"
                                + all[i].getParameterTypes().length);
                    }
                }
            } catch (Throwable ignored) {
                // this class is not ours to look at
            }
        }
        put(json, "fields", fields);
        put(json, "methods", methods);
        return json;
    }

    private static boolean useful(String name) {
        String text = name.toLowerCase();
        return text.contains("compos") || text.contains("coord") || text.contains("slot")
                || text.contains("root") || text.contains("node") || text.contains("origin")
                || text.contains("child") || text.contains("group") || text.contains("source")
                || text.contains("bounds") || text.contains("rect") || text.contains("size")
                || text.contains("semant") || text.contains("track");
    }
}
