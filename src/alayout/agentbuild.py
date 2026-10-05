"""Assembling the agent that reads the real composable names out of a debuggable app.

`am instrument` only runs an Instrumentation inside another app's process when that app is named by
android:targetPackage, so the (tiny) agent APK is linked for one app at a time: the Java sources in
`resources/agent` are compiled with javac and d8, aapt2 links the manifest with the app's package
filled in, and the result is zipaligned and signed with a throw-away debug key. Everything is kept
in alayout's cache directory, so inspecting the same app again only installs it. Needs a JDK and
Android SDK build-tools (aapt2 is already needed for `--apk`; Android Studio ships both).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from alayout.adb import sdk_dirs
from alayout.apk import _version_key, find_aapt2

AGENT_PACKAGE = "com.alayout.agent"
AGENT_INSTRUMENTATION = f"{AGENT_PACKAGE}.AlayoutAgent"
AGENT_DIR = Path(__file__).parent / "resources" / "agent"
MANIFEST_TEMPLATE = AGENT_DIR / "AndroidManifest.xml"
SOURCES_DIR = AGENT_DIR / "src"
TARGET_PLACEHOLDER = "@TARGET_PACKAGE@"
MIN_SDK = "21"
TARGET_SDK = "33"
KEY_ALIAS = "alayout"
KEY_PASSWORD = "alayout"
EXE = ".exe" if os.name == "nt" else ""

Runner = Callable[[list[str], float], bytes]


class AgentError(Exception):
    """The agent APK could not be assembled: no JDK, no build-tools, or a tool refused."""


@dataclass(frozen=True)
class AgentTools:
    java: Path
    javac: Path
    aapt2: Path
    zipalign: Path
    android_jar: Path

    @property
    def build_tools(self) -> Path:
        return self.aapt2.parent

    def jar(self, name: str) -> Path:
        for candidate in (self.build_tools / "lib" / name, self.build_tools / name):
            if candidate.is_file():
                return candidate
        raise AgentError(f"{name} is not in {self.build_tools}; install Android SDK build-tools")


def _run(args: list[str], timeout: float) -> bytes:
    try:
        proc = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AgentError(f"cannot run {Path(str(args[0])).name}: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise AgentError(f"{Path(str(args[0])).name} failed: {detail or proc.returncode}")
    return proc.stdout


def find_java(env: Mapping[str, str] | None = None,
              which: Callable[[str], str | None] = shutil.which) -> Path:
    env = os.environ if env is None else env
    home = env.get("JAVA_HOME")
    if home:
        for candidate in (Path(home) / "bin" / f"java{EXE}", Path(home) / f"java{EXE}"):
            if candidate.is_file():
                return candidate
    found = which("java")
    if found:
        return Path(found)
    raise AgentError("java not found: the composable-name agent is assembled with javac, d8 and "
                     "apksigner, so a JDK is needed (Android Studio ships one: set JAVA_HOME to "
                     "its jbr/Contents/Home folder)")


def find_android_jar(adb_path: Path | None = None, env: Mapping[str, str] | None = None,
                     cwd: Path | None = None) -> Path:
    for sdk in sdk_dirs(adb_path, env, cwd):
        found = sorted((sdk / "platforms").glob(f"android-*/android.jar"),
                       key=_version_key, reverse=True)
        if found:
            return found[0]
    raise AgentError("android.jar not found: install an Android SDK platform (SDK Manager -> "
                     "SDK Platforms) or pass --adb with a path inside that SDK")


def java_home_of(java: Path) -> Path | None:
    """Ask `java` which runtime it really is: on Windows `java` on the PATH is often a stub in
    `Oracle\\Java\\javapath`, and javac/keytool live in the JDK it launches (java.home)."""
    try:
        proc = subprocess.run([str(java), "-XshowSettings:properties", "-version"],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stderr + proc.stdout).decode("utf-8", "replace")
    found = re.search(r"java\.home\s*=\s*(.+)", text)
    return Path(found.group(1).strip()) if found else None


def _jdk_dirs(java: Path, env: Mapping[str, str]) -> list[Path]:
    home = java_home_of(java)
    candidates = [java.parent]
    if home:
        candidates += [home / "bin", home, home.parent / "bin"]
    if env.get("JAVA_HOME"):
        candidates += [Path(env["JAVA_HOME"]) / "bin", Path(env["JAVA_HOME"])]
    found: list[Path] = []
    for candidate in candidates:
        if candidate not in found and candidate.is_dir():
            found.append(candidate)
    return found


def _jdk_tool(name: str, java: Path, env: Mapping[str, str],
              which: Callable[[str], str | None] = shutil.which) -> Path:
    for directory in _jdk_dirs(java, env):
        candidate = directory / f"{name}{EXE}"
        if candidate.is_file():
            return candidate
    on_path = which(name)
    if on_path:
        return Path(on_path)
    raise AgentError(f"{name} not found next to {java}: alayout needs a JDK (javac, d8, keytool), "
                     "not only a JRE; Android Studio ships one in its jbr folder")


def find_tools(adb_path: str | Path | None = None, env: Mapping[str, str] | None = None,
               cwd: Path | None = None,
               which: Callable[[str], str | None] = shutil.which) -> AgentTools:
    env = os.environ if env is None else env
    java = find_java(env, which)
    aapt2 = find_aapt2(Path(adb_path) if adb_path else None, env, cwd)
    return AgentTools(java=java, javac=_jdk_tool("javac", java, env, which), aapt2=aapt2,
                      zipalign=_build_tool(aapt2.parent, "zipalign"),
                      android_jar=find_android_jar(Path(adb_path) if adb_path else None, env, cwd))


def _build_tool(build_tools: Path, name: str) -> Path:
    candidate = build_tools / f"{name}{EXE}"
    if candidate.is_file():
        return candidate
    raise AgentError(f"{name} is not in {build_tools}; install Android SDK build-tools")



def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where built agent APKs and the signing key live; ALAYOUT_CACHE overrides it."""
    env = os.environ if env is None else env
    if env.get("ALAYOUT_CACHE"):
        return Path(env["ALAYOUT_CACHE"])
    if env.get("XDG_CACHE_HOME"):
        return Path(env["XDG_CACHE_HOME"]) / "alayout"
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "alayout"
    home = env.get("HOME") or env.get("USERPROFILE")
    return Path(home) / ".cache" / "alayout" if home else Path(".alayout-cache")


def agent_apk(package: str, cache: Path | None = None, key: str = "debug") -> Path:
    """The signed agent lives in a folder named after the app, so a repeat run reuses it."""
    return (cache or cache_dir()) / package / f"agent-{key}.apk"


@dataclass(frozen=True)
class SigningKey:
    """A keystore to sign the agent with.

    Most devices only let an app be instrumented by a package signed with the *same* key (the
    "target is debuggable" exception in AOSP is not honoured by several OEM builds), and a debug
    build is nearly always signed with the machine's `~/.android/debug.keystore` -- so that key is
    tried first, then alayout's own throw-away key. A keystore and the key inside it can have
    different passwords (apksigner's `--ks-pass` vs `--key-pass`), so the two are kept apart."""

    keystore: Path
    alias: str
    store_password: str
    key_password: str
    name: str


def signing_override(keystore: str | Path | None, alias: str | None = None,
                     key_password: str | None = None,
                     store_password: str | None = None) -> SigningKey | None:
    """Build the one key chosen explicitly (CLI flags), or None when no keystore was given.

    Defaults match a debug keystore (alias `androiddebugkey`, password `android`); the store
    password falls back to the key password, the common case for a single-key keystore."""
    if not keystore:
        return None
    key_pw = key_password or "android"
    return SigningKey(Path(keystore), alias or "androiddebugkey",
                      store_password or key_pw, key_pw, "custom")


def signing_keys(cache: Path | None = None, env: Mapping[str, str] | None = None,
                 override: SigningKey | None = None) -> list[SigningKey]:
    """The keys to try, best first. An explicit `override` (CLI flags) wins; otherwise
    ALAYOUT_KEYSTORE (+ _ALIAS/_PASSWORD/_STORE_PASSWORD) picks one, else the debug and alayout
    keys are tried in turn."""
    if override is not None:
        return [override]
    env = os.environ if env is None else env
    cache = cache or cache_dir(env)
    if env.get("ALAYOUT_KEYSTORE"):
        key_pw = env.get("ALAYOUT_KEY_PASSWORD", "android")
        store_pw = env.get("ALAYOUT_KEY_STORE_PASSWORD", key_pw)
        return [SigningKey(Path(env["ALAYOUT_KEYSTORE"]), env.get("ALAYOUT_KEY_ALIAS", "androiddebugkey"),
                          store_pw, key_pw, "custom")]
    keys = []
    debug = Path.home() / ".android" / "debug.keystore"
    if debug.is_file():
        keys.append(SigningKey(debug, "androiddebugkey", "android", "android", "debug"))
    keys.append(SigningKey(cache / "alayout.keystore", KEY_ALIAS, KEY_PASSWORD, KEY_PASSWORD, "alayout"))
    return keys


def _agent_sources() -> list[Path]:
    return [MANIFEST_TEMPLATE, *sorted(SOURCES_DIR.rglob("*.java"))]


def _needs_build(apk: Path) -> bool:
    """Rebuild when the APK is missing or older than the agent's own sources."""
    if not apk.is_file():
        return True
    stamp = apk.stat().st_mtime
    return any(path.stat().st_mtime > stamp for path in _agent_sources())


def _check_package(package: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+", package or ""):
        raise AgentError(f"{package!r} is not a package name")


def _manifest(package: str, into: Path) -> Path:
    template = MANIFEST_TEMPLATE.read_text(encoding="utf-8")
    if TARGET_PLACEHOLDER not in template:
        raise AgentError(f"{MANIFEST_TEMPLATE} has no {TARGET_PLACEHOLDER} to replace")
    target = into / "AndroidManifest.xml"
    target.write_text(template.replace(TARGET_PLACEHOLDER, package), encoding="utf-8")
    return target


def _ensure_keystore(key: SigningKey, tools: AgentTools, run: Runner) -> None:
    """alayout's own throw-away key is created on first use; any other key must already exist."""
    if key.keystore.is_file():
        return
    if key.name != "alayout":
        raise AgentError(f"{key.keystore} (keystore '{key.name}') does not exist")
    keytool = _jdk_tool("keytool", tools.java, os.environ)
    key.keystore.parent.mkdir(parents=True, exist_ok=True)
    run([str(keytool), "-genkeypair", "-alias", key.alias, "-keyalg", "RSA", "-keysize", "2048",
         "-validity", "7300", "-keystore", str(key.keystore), "-storepass", key.store_password,
         "-keypass", key.key_password, "-dname", "CN=alayout agent", "-noprompt"], 120.0)


def build_agent(package: str, key: SigningKey | None = None, tools: AgentTools | None = None,
                cache: Path | None = None, adb_path: str | Path | None = None,
                run: Runner | None = None) -> Path:
    """The agent APK for `package`, signed with `key`, built once and kept in the cache."""
    _check_package(package)
    run = run or _run
    cache = cache or cache_dir()
    key = key or signing_keys(cache)[0]
    out = agent_apk(package, cache, key.name)
    if not _needs_build(out):
        return out
    tools = tools or find_tools(adb_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / f"build-{key.name}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    classes, dex = work / "classes", work / "dex"
    classes.mkdir(parents=True, exist_ok=True)
    dex.mkdir(parents=True, exist_ok=True)

    sources = [str(path) for path in SOURCES_DIR.rglob("*.java")]
    error: AgentError | None = None
    for before, after in ((["-source", "1.8", "-target", "1.8", "-bootclasspath", str(tools.android_jar)], []),
                          (["--release", "8", "-classpath", str(tools.android_jar)], []),
                          ([], ["-classpath", str(tools.android_jar)])):
        # a JDK that dropped -source 8 (or refuses -bootclasspath with a newer target) still
        # compiles against android.jar as a plain classpath
        try:
            run([str(tools.javac), "-nowarn", *before, "-d", str(classes), *sources, *after], 300.0)
            error = None
            break
        except AgentError as e:
            error = e
    if error is not None:
        raise error

    run([str(tools.java), "-cp", str(tools.jar("d8.jar")), "com.android.tools.r8.D8", "--release",
         "--min-api", MIN_SDK, "--lib", str(tools.android_jar), "--output", str(dex),
         *[str(c) for c in sorted(classes.rglob("*.class"))]], 300.0)
    created = dex / "classes.dex"
    if not created.is_file():
        raise AgentError(f"d8 produced no classes.dex in {dex}")

    base = work / "base.apk"
    run([str(tools.aapt2), "link", "--manifest", str(_manifest(package, work)),
         "-I", str(tools.android_jar), "--min-sdk-version", MIN_SDK,
         "--target-sdk-version", TARGET_SDK, "-o", str(base)], 120.0)
    merged = work / "merged.apk"
    with zipfile.ZipFile(base) as source, zipfile.ZipFile(merged, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.write(created, "classes.dex")
    aligned = work / "aligned.apk"
    run([str(tools.zipalign), "-f", "4", str(merged), str(aligned)], 120.0)
    _ensure_keystore(key, tools, run)
    run([str(tools.java), "-jar", str(tools.jar("apksigner.jar")), "sign", "--ks",
         str(key.keystore), "--ks-key-alias", key.alias,
         "--ks-pass", f"pass:{key.store_password}", "--key-pass", f"pass:{key.key_password}",
         "--out", str(out), str(aligned)], 120.0)
    if not out.is_file():
        raise AgentError(f"apksigner wrote no {out}")
    return out
