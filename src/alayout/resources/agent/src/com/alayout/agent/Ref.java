package com.alayout.agent;

import java.lang.reflect.Array;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * Reflection that survives Kotlin's name mangling and Compose's moving internals.
 *
 * `internal` members are compiled as `name$module` and tooling-mangled ones as `name-hash`, and
 * both the names and the shapes of the Compose internals move between versions (a List of nodes
 * here, a MutableVector or an IntObjectMap there). Every look-up here compares the leading part of
 * the name, takes a list of candidates and never throws: a Compose version we do not understand
 * costs information, never the app.
 */
final class Ref {

    private static final Map<String, Object> CACHE = new HashMap<String, Object>();
    private static final Object NONE = new Object();

    private Ref() { }

    /** `getChildren$ui_release` and `boundsInWindow-UEl5yQU` both become the name Kotlin started
      * from, which is what we compare. */
    static String base(String name) {
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            if (c == '$' || c == '-') {
                return name.substring(0, i);
            }
        }
        return name;
    }

    /** The first zero-argument method of `target` named one of `names` that answers something. */
    static Object call(Object target, String... names) {
        if (target == null) {
            return null;
        }
        Method method = method(target.getClass(), names, false);
        if (method == null) {
            return null;
        }
        try {
            return method.invoke(target);
        } catch (Throwable ignored) {
            return null;
        }
    }

    static Object callStatic(Class<?> owner, String... names) {
        Method method = method(owner, names, true);
        if (method == null) {
            return null;
        }
        try {
            return method.invoke(null);
        } catch (Throwable ignored) {
            return null;
        }
    }

    /** Run a zero-argument method that returns nothing: true when one was found and ran. */
    static boolean run(Object target, String... names) {
        return invoke(target, names, null, 0);
    }

    /** Run a void method that takes one int (`invalidateGroupsWithKey(-1)`). */
    static boolean runInt(Object target, int value, String... names) {
        return invoke(target, names, Integer.valueOf(value), 1);
    }

    /** Call a method named one of `names` that takes a single int and returns something
      * (`SlotTable.node(i)`, `isNode(i)`, `parentOf(i)`, `sourceInformationOf(i)`). */
    static Object callInt(Object target, int value, String... names) {
        if (target == null) {
            return null;
        }
        Method method = intMethod(target.getClass(), names);
        if (method == null) {
            return null;
        }
        try {
            return method.invoke(target, Integer.valueOf(value));
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static Method intMethod(Class<?> owner, String[] names) {
        String key = owner.getName() + "#i1#" + join(names);
        Object cached = CACHE.get(key);
        if (cached != null) {
            return cached == NONE ? null : (Method) cached;
        }
        Method found = null;
        List<Class<?>> queue = new ArrayList<Class<?>>();
        queue.add(owner);
        for (int q = 0; q < queue.size() && q < 300 && found == null; q++) {
            Class<?> c = queue.get(q);
            Method[] declared;
            try {
                declared = c.getDeclaredMethods();
            } catch (Throwable t) {
                continue;
            }
            for (int i = 0; i < declared.length; i++) {
                Method method = declared[i];
                Class<?>[] parameters = method.getParameterTypes();
                if (Modifier.isStatic(method.getModifiers()) || parameters.length != 1
                        || (parameters[0] != int.class && parameters[0] != Integer.class)
                        || method.getReturnType() == void.class
                        || !matches(method.getName(), names)) {
                    continue;
                }
                found = method;
                try {
                    found.setAccessible(true);
                } catch (Throwable ignored) {
                    // as declared
                }
                break;
            }
            Class<?> superclass = c.getSuperclass();
            if (superclass != null && !queue.contains(superclass)) {
                queue.add(superclass);
            }
        }
        CACHE.put(key, found == null ? NONE : found);
        return found;
    }

    /** A new instance of `className` built from `arguments` (floats find the float constructor). */
    static Object newInstance(String className, Object... arguments) {
        Class<?> type;
        try {
            type = Class.forName(className);
        } catch (Throwable e) {
            return null;
        }
        try {
            Class<?>[] parameters = new Class<?>[arguments.length];
            for (int i = 0; i < arguments.length; i++) {
                parameters[i] = primitiveOf(arguments[i]);
            }
            Constructor<?> constructor = type.getConstructor(parameters);
            constructor.setAccessible(true);
            return constructor.newInstance(arguments);
        } catch (Throwable ignored) {
            return null;
        }
    }

    /** A static method of `className` named `name`, matched by argument count (a boxed Float or
      * Integer matches a float/int parameter): reaches a Kotlin top-level factory like
      * `androidx.compose.ui.unit.DensityKt.Density(density, fontScale)`. */
    static Object staticCall(String className, String name, Object... arguments) {
        Class<?> type;
        try {
            type = Class.forName(className);
        } catch (Throwable e) {
            return null;
        }
        try {
            Method[] declared = type.getDeclaredMethods();
            for (int i = 0; i < declared.length; i++) {
                Method method = declared[i];
                if (!Modifier.isStatic(method.getModifiers())
                        || method.getParameterTypes().length != arguments.length
                        || !matches(method.getName(), new String[] {name})) {
                    continue;
                }
                method.setAccessible(true);
                return method.invoke(null, arguments);
            }
        } catch (Throwable ignored) {
            // no such factory on this Compose version: the caller falls back
        }
        return null;
    }

    /** Invoke a static method of `className` named `name` for its side effect (matched by argument
      * count): true when one was found and ran. Reaches `HotReloaderKt.simulateHotReload(context)`. */
    static boolean staticRun(String className, String name, Object... arguments) {
        Class<?> type;
        try {
            type = Class.forName(className);
        } catch (Throwable e) {
            return false;
        }
        try {
            Method[] declared = type.getDeclaredMethods();
            for (int i = 0; i < declared.length; i++) {
                Method method = declared[i];
                if (!Modifier.isStatic(method.getModifiers())
                        || method.getParameterTypes().length != arguments.length
                        || !matches(method.getName(), new String[] {name})) {
                    continue;
                }
                method.setAccessible(true);
                method.invoke(null, arguments);
                return true;
            }
        } catch (Throwable ignored) {
            // the hot-reload entry point is not on this Compose version: the caller reports it
        }
        return false;
    }

    private static Class<?> primitiveOf(Object value) {
        if (value instanceof Float) {
            return float.class;
        }
        if (value instanceof Integer) {
            return int.class;
        }
        if (value instanceof Boolean) {
            return boolean.class;
        }
        return value.getClass();
    }

    /** Call a void instance method that takes this one argument (`Recomposer.invalidate(composition)`). */
    static boolean runWith(Object target, Object argument, String... names) {
        return invokeWith(target, argument, names);
    }

    private static boolean invokeWith(Object target, Object argument, String[] names) {
        if (target == null || argument == null) {
            return false;
        }
        Method method = singleArg(target.getClass(), argument, names, false, void.class);
        if (method == null) {
            return false;
        }
        try {
            method.invoke(target, argument);
            return true;
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static boolean invoke(Object target, String[] names, Object value, int args) {
        if (target == null) {
            return false;
        }
        Method method = voidMethod(target.getClass(), names, args);
        if (method == null) {
            return false;
        }
        try {
            if (args == 0) {
                method.invoke(target);
            } else {
                method.invoke(target, value);
            }
            return true;
        } catch (Throwable ignored) {
            return false;
        }
    }

    /** A void method with no more than `args` parameters, found by the name Kotlin started from. */
    private static Method voidMethod(Class<?> owner, String[] names, int args) {
        String key = owner.getName() + "#v#" + args + "#" + join(names);
        Object cached = CACHE.get(key);
        if (cached != null) {
            return cached == NONE ? null : (Method) cached;
        }
        Method found = null;
        List<Class<?>> queue = new ArrayList<Class<?>>();
        queue.add(owner);
        for (int i = 0; i < queue.size() && i < 300; i++) {
            Class<?> c = queue.get(i);
            Method[] declared;
            try {
                declared = c.getDeclaredMethods();
            } catch (Throwable t) {
                continue;
            }
            for (int m = 0; m < declared.length; m++) {
                Method method = declared[m];
                Class<?>[] parameters = method.getParameterTypes();
                if (method.getReturnType() != void.class
                        || Modifier.isStatic(method.getModifiers())
                        || parameters.length != args
                        || (args == 1 && parameters[0] != int.class && parameters[0] != Integer.class)
                        || !matches(method.getName(), names)) {
                    continue;
                }
                found = method;
                try {
                    found.setAccessible(true);
                } catch (Throwable ignored) {
                    // as declared
                }
                break;
            }
            if (found != null) {
                break;
            }
            Class<?> superclass = c.getSuperclass();
            if (superclass != null && !queue.contains(superclass)) {
                queue.add(superclass);
            }
            Class<?>[] interfaces = c.getInterfaces();
            for (int x = 0; x < interfaces.length; x++) {
                if (!queue.contains(interfaces[x])) {
                    queue.add(interfaces[x]);
                }
            }
        }
        CACHE.put(key, found == null ? NONE : found);
        return found;
    }

    /** A Kotlin extension function: `fun LayoutCoordinates.boundsInWindow()` is compiled into a
      * static `...Kt.boundsInWindow($this)` that takes the receiver as its only argument. */
    static Object extension(Object target, String owner, String... names) {
        if (target == null) {
            return null;
        }
        Class<?> type;
        try {
            type = Class.forName(owner);
        } catch (Throwable e) {
            return null;
        }
        Method method = singleArg(type, target, names, true, null);
        if (method == null) {
            return null;
        }
        try {
            return method.invoke(null, target);
        } catch (Throwable ignored) {
            return null;
        }
    }

    /** A one-argument method named one of `names`, static (an extension function) or instance, with
      * the given return type (`null` for any). */
    private static Method singleArg(Class<?> owner, Object argument, String[] names, boolean statics,
                                    Class<?> returns) {
        String key = owner.getName() + "#a#" + (statics ? "s" : "i") + "#"
                + (returns == null ? "*" : returns.getName()) + "#"
                + argument.getClass().getName() + "#" + join(names);
        Object cached = CACHE.get(key);
        if (cached != null) {
            return cached == NONE ? null : (Method) cached;
        }
        Method found = null;
        // the setter we want (`SnapshotMutableStateImpl.setValue`) is often inherited by the concrete
        // class we hold (`ParcelableSnapshotMutableState`), so the whole hierarchy is searched, not
        // only the class's own declared methods
        List<Class<?>> queue = new ArrayList<Class<?>>();
        queue.add(owner);
        for (int q = 0; q < queue.size() && q < 300 && found == null; q++) {
            Class<?> c = queue.get(q);
            Method[] declared;
            try {
                declared = c.getDeclaredMethods();
            } catch (Throwable t) {
                continue;
            }
            for (int i = 0; i < declared.length; i++) {
                Method method = declared[i];
                if (Modifier.isStatic(method.getModifiers()) != statics
                        || method.getParameterTypes().length != 1
                        || !method.getParameterTypes()[0].isInstance(argument)
                        || (returns != null && method.getReturnType() != returns)
                        || !matches(method.getName(), names)) {
                    continue;
                }
                found = method;
                try {
                    found.setAccessible(true);
                } catch (Throwable ignored) {
                    // as declared
                }
                break;
            }
            Class<?> superclass = c.getSuperclass();
            if (superclass != null && !queue.contains(superclass)) {
                queue.add(superclass);
            }
            Class<?>[] interfaces = c.getInterfaces();
            for (int x = 0; x < interfaces.length; x++) {
                if (!queue.contains(interfaces[x])) {
                    queue.add(interfaces[x]);
                }
            }
        }
        CACHE.put(key, found == null ? NONE : found);
        return found;
    }

    /** The value of the first instance field named one of `names` that holds something. */
    static Object field(Object target, String... names) {
        if (target == null) {
            return null;
        }
        for (Class<?> c = target.getClass(); c != null; c = c.getSuperclass()) {
            for (int i = 0; i < names.length; i++) {
                Field found = fields(c, names[i]);
                if (found == null) {
                    continue;
                }
                try {
                    Object value = found.get(target);
                    if (value != null) {
                        return value;
                    }
                } catch (Throwable ignored) {
                    // try the next name
                }
            }
        }
        return null;
    }

    /** The first field of `target` whose value answers one of `names`: finds the composition whose
      * field name we do not know (`composition`, `compositionContext`, ...) by what it can do. */
    static Object findByMethod(Object target, String... names) {
        if (target == null) {
            return null;
        }
        List<Object> values = values(target);
        for (int i = 0; i < values.size(); i++) {
            Object found = call(values.get(i), names);
            if (found != null) {
                return found;
            }
        }
        return null;
    }

    /** A List of whatever `value` holds: Collection, Map values, array, Iterable or Iterator. */
    static List<Object> items(Object value) {
        List<Object> out = new ArrayList<Object>();
        if (value == null) {
            return out;
        }
        try {
            if (value instanceof Collection) {
                out.addAll((Collection<?>) value);
            } else if (value instanceof Map) {
                out.addAll(((Map<?, ?>) value).values());
            } else if (value.getClass().isArray()) {
                for (int i = 0; i < Array.getLength(value); i++) {
                    out.add(Array.get(value, i));
                }
            } else if (value instanceof Iterable) {
                for (Object item : (Iterable<?>) value) {
                    out.add(item);
                }
            } else if (value instanceof Iterator) {
                Iterator<?> it = (Iterator<?>) value;
                while (it.hasNext()) {
                    out.add(it.next());
                }
            }
        } catch (Throwable ignored) {
            // a live collection changed under us: keep what we have
        }
        for (int i = out.size() - 1; i >= 0; i--) {
            if (out.get(i) == null) {
                out.remove(i);
            }
        }
        return out;
    }

    /** A number out of a Compose value class: `Rect.left` as a field, or through getLeft(). */
    static float number(Object target, String name) {
        Object value = null;
        try {
            Field f = target.getClass().getDeclaredField(name);
            f.setAccessible(true);
            value = f.get(target);
        } catch (Throwable ignored) {
            value = null;
        }
        if (value == null) {
            String getter = "get" + Character.toUpperCase(name.charAt(0)) + name.substring(1);
            value = call(target, getter);
        }
        return value instanceof Number ? ((Number) value).floatValue() : Float.NaN;
    }

    static String text(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    /** Every non-null instance field value of `target` and of its superclasses. */
    static List<Object> values(Object target) {
        List<Object> out = new ArrayList<Object>();
        if (target == null) {
            return out;
        }
        for (Class<?> c = target.getClass(); c != null; c = c.getSuperclass()) {
            Field[] declared;
            try {
                declared = c.getDeclaredFields();
            } catch (Throwable t) {
                return out;
            }
            for (int i = 0; i < declared.length; i++) {
                Field f = declared[i];
                Class<?> type = f.getType();
                if (Modifier.isStatic(f.getModifiers()) || type.isPrimitive()
                        || type == String.class || Number.class.isAssignableFrom(type)) {
                    continue;
                }
                try {
                    f.setAccessible(true);
                    Object value = f.get(target);
                    if (value != null && value != target) {
                        out.add(value);
                    }
                } catch (Throwable ignored) {
                    // unreadable
                }
            }
        }
        return out;
    }

    private static Method method(Class<?> owner, String[] names, boolean statics) {
        String key = owner.getName() + "#m#" + join(names) + (statics ? "#s" : "");
        Object cached = CACHE.get(key);
        if (cached != null) {
            return cached == NONE ? null : (Method) cached;
        }
        Method found = search(owner, names, statics);
        CACHE.put(key, found == null ? NONE : found);
        return found;
    }

    /** The class, its superclasses and the interfaces they implement, in that order. */
    private static Method search(Class<?> owner, String[] names, boolean statics) {
        List<Class<?>> queue = new ArrayList<Class<?>>();
        queue.add(owner);
        for (int i = 0; i < queue.size() && i < 300; i++) {
            Class<?> c = queue.get(i);
            Method[] declared;
            try {
                declared = c.getDeclaredMethods();
            } catch (Throwable t) {
                continue;
            }
            for (int m = 0; m < declared.length; m++) {
                Method method = declared[m];
                if (method.getParameterTypes().length != 0
                        || Modifier.isStatic(method.getModifiers()) != statics
                        || !matches(method.getName(), names)) {
                    continue;
                }
                try {
                    method.setAccessible(true);
                } catch (Throwable ignored) {
                    // a public method of a public class stays reachable
                }
                return method;
            }
            Class<?> superclass = c.getSuperclass();
            if (superclass != null && !queue.contains(superclass)) {
                queue.add(superclass);
            }
            Class<?>[] interfaces = c.getInterfaces();
            for (int x = 0; x < interfaces.length; x++) {
                if (!queue.contains(interfaces[x])) {
                    queue.add(interfaces[x]);
                }
            }
        }
        return null;
    }

    private static Field fields(Class<?> owner, String name) {
        String key = owner.getName() + "#f#" + name;
        Object cached = CACHE.get(key);
        if (cached != null) {
            return cached == NONE ? null : (Field) cached;
        }
        Field found = null;
        try {
            Field[] declared = owner.getDeclaredFields();
            for (int i = 0; i < declared.length; i++) {
                if (!Modifier.isStatic(declared[i].getModifiers())
                        && base(declared[i].getName()).equals(name)) {
                    found = declared[i];
                    found.setAccessible(true);
                    break;
                }
            }
        } catch (Throwable ignored) {
            found = null;
        }
        CACHE.put(key, found == null ? NONE : found);
        return found;
    }

    private static boolean matches(String methodName, String[] names) {
        String base = base(methodName);
        for (int i = 0; i < names.length; i++) {
            if (base.equals(names[i])) {
                return true;
            }
        }
        return false;
    }

    private static String join(String[] names) {
        StringBuilder text = new StringBuilder();
        for (int i = 0; i < names.length; i++) {
            text.append(names[i]).append(',');
        }
        return text.toString();
    }
}

