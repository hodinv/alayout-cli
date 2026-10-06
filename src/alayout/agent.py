"""Reading the real composable names: alayout's agent, run inside the debuggable app.

uiautomator and dumpsys cannot tell which composable drew a box (Compose keeps no names in the
semantics tree), so for a debuggable app alayout injects `resources/agent` with `am instrument`,
restarts the app, lets the user open the window they want and asks the app itself for its Compose
tree -- names, source files and bounds, straight out of the slot table of a debug build. The two
processes talk through files in the app's own data directory, which `run-as` reaches for exactly
the apps that can be instrumented (debuggable ones). See `agentbuild` for how the APK is assembled.
"""

from __future__ import annotations

import itertools
import json
import re
import shlex
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple, Protocol

from alayout.adb import AdbError
from alayout.agentbuild import (AGENT_INSTRUMENTATION, AGENT_PACKAGE, AgentError, SigningKey,
                                build_agent, cache_dir, signing_keys)
from alayout.composables import parse_source_information
from alayout.compose import GENERIC_COMPOSABLES, STRUCTURAL_COMPOSABLES
from alayout.model import Rect
from alayout.parse.dumpsys import parse_dumpsys

DATA_DIR = "/data/data"
REQUEST_FILE = "alayout_request"
DUMP_FILE = "alayout_compose.json"
POLL_S = 0.25
DEFAULT_AGENT_TIMEOUT = 1800.0
KEY_HINT = ("Android only lets one app instrument another when the two are signed with the same "
            "key; a debug build is signed with ~/.android/debug.keystore. If this app uses another "
            "keystore, say so: ALAYOUT_KEYSTORE=path ALAYOUT_KEY_ALIAS=alias "
            "ALAYOUT_KEY_PASSWORD=password alayout --compose (or --keystore/--key-alias/"
            "--key-password/--key-store-password)")


class SignatureRefused(AgentError):
    """The device would not let this agent (this signing key) into the app's process."""


class AgentDevice(Protocol):
    """What `Adb` gives us: a shell, an installer and the SDK path the device was found through."""

    serial: str

    def exec_out(self, cmd: str, timeout: float = 30.0) -> bytes: ...

    def install(self, apk: Path, timeout: float = 300.0) -> str: ...

    def uninstall(self, package: str, timeout: float = 120.0) -> None: ...


@dataclass(frozen=True)
class ComposeHit:
    """One Compose layout node as the app reported it: where it is and who made it."""

    bounds: Rect | None
    name: str | None
    file: str | None
    line: int | None
    path: tuple[str, ...] = ()
    path_ids: tuple[int, ...] = ()  # one stable group id per path element (see ComposeDump.pathOf)
    text: str | None = None  # the literal string a text node draws, read from the node's modifier
    source_info: str = ""


@dataclass
class AgentDump:
    hits: list[ComposeHit] = field(default_factory=list)
    windows: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    package: str | None = None
    process: str | None = None

    @property
    def named(self) -> list[ComposeHit]:
        return [hit for hit in self.hits if hit.name and hit.bounds]


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace").replace("\r\n", "\n")


def request_path(package: str) -> str:
    return f"{DATA_DIR}/{package}/cache/{REQUEST_FILE}"


def dump_path(package: str) -> str:
    return f"{DATA_DIR}/{package}/cache/{DUMP_FILE}"


def foreground(adb: AgentDevice) -> tuple[str | None, str | None]:
    """(package, activity) of the resumed activity, from `dumpsys activity top`."""
    dump = parse_dumpsys(_text(adb.exec_out("dumpsys activity top")))
    return (dump.package, dump.activity) if dump else (None, None)


def is_debuggable(adb: AgentDevice, package: str) -> bool:
    """Only a debuggable app lets another package run code inside it, and only it has a slot table."""
    text = _text(adb.exec_out(f"dumpsys package {package}", timeout=60.0))
    return bool(re.search(r"(?:pkg)?[Ff]lags=\[[^\]]*DEBUGGABLE", text))


_STRUCTURAL = STRUCTURAL_COMPOSABLES  # pass-through wrappers, never a useful headline
_TEXT_LEAVES = frozenset({"Text"})


def _compose_name(path: tuple[str, ...], parsed: tuple[str, str, int | None] | None) -> str | None:
    """The composable to headline for a layout node.

    The node's own group is usually positional (`C89@...`, no name), so the name comes from the call
    path. The app/component name is the most telling, so pass-through wrappers are dropped and then,
    unless the nearest composable is a text primitive, generic building blocks (`Box`, `Image`, ...)
    are climbed past to the app or Material component that drew the box (`MoneyCard`'s box reads
    `Card`, a `ProgressCircle`'s `Image` reads `ProgressCircle`). The full path is kept separately.
    """
    if parsed:
        return parsed[0]
    meaningful = [part for part in path if part not in _STRUCTURAL]
    if not meaningful:
        return path[-1] if path else None
    leaf = meaningful[-1]
    if leaf in _TEXT_LEAVES:
        return leaf
    for part in reversed(meaningful):
        if part not in GENERIC_COMPOSABLES:
            return part
    return leaf


def parse_agent_dump(text: str) -> AgentDump:
    """The agent's JSON: every layout node of every window that shows Compose."""
    dump = AgentDump()
    try:
        data = json.loads(text)
    except ValueError as e:
        raise AgentError(f"the agent wrote invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise AgentError(f"unexpected agent answer: {text[:80]}")
    dump.package = data.get("package")
    dump.process = data.get("process")
    dump.errors = [str(e) for e in data.get("errors") or ()]
    for window in data.get("windows") or ():
        title = window.get("activity") or window.get("root") or "window"
        location = f"({window.get('left', 0)},{window.get('top', 0)})"
        dump.windows.append(f"{title} {location}")
        for view in window.get("composeViews") or ():
            for node in view.get("nodes") or ():
                bounds = node.get("bounds")
                info = node.get("sourceInfo") or ""
                parsed = parse_source_information(info) if info else None
                path = tuple(str(part) for part in node.get("path") or ())
                path_ids = tuple(int(v) for v in node.get("pathIds") or ())
                if len(path_ids) != len(path):
                    path_ids = ()  # only trust ids that line up one-to-one with the names
                dump.hits.append(ComposeHit(
                    bounds=Rect.from_list([int(v) for v in bounds]) if bounds else None,
                    name=_compose_name(path, parsed),
                    file=parsed[1] if parsed else None,
                    line=parsed[2] if parsed else None,
                    path=path or ((parsed[0],) if parsed else ()),
                    path_ids=path_ids,
                    text=(str(node["text"]) if node.get("text") else None),
                    source_info=info))
    return dump


def _run_as(adb: AgentDevice, package: str, command: str, timeout: float = 60.0) -> bytes:
    """`run-as` only works for debuggable apps -- exactly the ones we can instrument."""
    return adb.exec_out(f"run-as {package} sh -c {shlex.quote(command)}", timeout)


def install_agent(adb: AgentDevice, apk: Path) -> None:
    """The agent is a package of its own, so the app under inspection is not modified on disk."""
    try:
        adb.install(apk)
    except AdbError as e:
        if "UPDATE_INCOMPATIBLE" not in str(e):
            raise AgentError(f"cannot install the composable-name agent: {e}") from e
        adb.uninstall(AGENT_PACKAGE)  # an agent built with another debug key (another machine)
        adb.install(apk)


def prepare(adb: AgentDevice, package: str) -> None:
    """A clean mailbox: an old request would make the new agent dump before we are ready."""
    _run_as(adb, package, f"mkdir -p {DATA_DIR}/{package}/cache;"
                          f" rm -f {request_path(package)} {dump_path(package)}")


_REQUEST_SEQ = itertools.count(int(time.time()))


def request(adb: AgentDevice, package: str, command: str) -> int:
    """Ask the agent to `prepare`, `dump` or `exit`; the number tells the host which answer is ours
    and makes each request a distinct line (the agent ignores a repeat of the line it last saw)."""
    seq = next(_REQUEST_SEQ)
    _run_as(adb, package, f"echo {seq} {command} > {request_path(package)}")
    return seq


def _send(adb: AgentDevice, package: str, command: str) -> int:
    """A request for a process we are about to leave: an app that already died is no error."""
    try:
        return request(adb, package, command)
    except AdbError:
        return 0


class Attachment:
    """The `am instrument -w` call that attaches the agent, kept in a background thread.

    `-w` is not optional on many devices: Android only hands the Instrumentation to the app when a
    watcher is registered, so without it `onStart()` is never called. `-w` then blocks until the
    agent finishes, which is exactly as long as the user is browsing -- hence the thread. A refusal
    (wrong signature, unknown class) is reported immediately, so `check()` right after the start is
    how we notice them.
    """

    def __init__(self, adb: AgentDevice, package: str, agent_timeout: float,
                 debug: bool = False):
        self.adb = adb
        self.package = package
        self.timeout = agent_timeout
        self.debug = debug
        self.answers: list[str] = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def command(self) -> str:
        extra = " -e debug true" if self.debug else ""
        return (f"am instrument -w -e request {request_path(self.package)}"
                f" -e out {dump_path(self.package)} -e timeout {int(self.timeout)}{extra}"
                f" {AGENT_PACKAGE}/{AGENT_INSTRUMENTATION}")

    def _run(self) -> None:
        try:
            self.answers.append(_text(self.adb.exec_out(self.command(), self.timeout + 120)))
        except AdbError as e:
            self.answers.append(str(e))

    def check(self, wait: float = 3.0) -> None:
        """Give the device a moment to say no, and turn whatever it said into an error."""
        self.thread.join(wait)
        text = " ".join(self.answers)
        if not text.strip():
            return
        first = next((line.strip() for line in text.splitlines() if line.strip()), text)
        if "signature matching" in text or "SecurityException" in text:
            raise SignatureRefused(first[:300])
        raise AgentError(first[:300])

    def release(self, timeout: float = 10.0) -> None:
        """After the agent was told to exit, wait for `am instrument` to come back."""
        self.thread.join(timeout)


def start(adb: AgentDevice, package: str, activity: str | None, apk: Path,
          agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
          debug: bool = False) -> Attachment:
    """Restart the app with the agent attached (the app loses its state: that is the deal)."""
    install_agent(adb, apk)
    prepare(adb, package)  # before attaching: a stale request would be answered before we are ready
    adb.exec_out(f"am force-stop {package}")
    attached = Attachment(adb, package, agent_timeout, debug)
    attached.check()
    # start the app at its launcher entry, not the activity that happened to be on top: after a
    # force-stop a deep inner activity often won't start cold (it needs its back stack / app init),
    # which leaves the screen blank. The user navigates to the screen they want next (collect's
    # wait()); the original activity is only used by finish() to restore the view afterwards.
    launch(adb, package, None)
    return attached


def launch(adb: AgentDevice, package: str, activity: str | None) -> None:
    if activity:
        adb.exec_out(f"am start -n {package}/{activity}")
    else:
        adb.exec_out(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")


def _prepare(adb: AgentDevice, package: str, log: Callable[[str], None],
             answer_timeout: float = 90.0, attempts: int = 4) -> None:
    """Turn Compose source-information collection on (and hot-reload once) *before* the user opens
    the screen, so the screen they navigate to records its names as it is composed and their
    navigation is kept -- a hot reload at capture time would reset a single-activity NavHost app to
    its start screen. Retried because the app may not have composed yet right after launch; a repeat
    is harmless (collection is idempotent and an already-collecting app answers at once)."""
    for attempt in range(attempts):
        seq = request(adb, package, "prepare")
        try:
            ack = json.loads(read_dump(adb, package, seq, answer_timeout))
        except (AgentError, ValueError):
            ack = {}
        if ack.get("prepared"):
            where = f" (process {ack['process']})" if ack.get("process") else ""
            log(f"composable names: collection enabled on {package}{where}")
            return
        if attempt + 1 < attempts:
            time.sleep(0.6)
    where = f" in process {ack['process']}" if ack.get("process") else ""
    log(f"composable names: no Compose window to prepare on {package}{where} yet; capturing as-is"
        " (if the app is multi-process, its UI may be in another process)")


def read_dump(adb: AgentDevice, package: str, seq: int, timeout: float = 60.0) -> str:
    """Wait for the agent's answer to request `seq`: it reads the tree on the app's main thread and
    writes it into the app's own cache directory, which we then read back."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            text = _text(adb.exec_out(f"run-as {package} cat {dump_path(package)}"))
        except AdbError:
            text = ""
        if text.lstrip().startswith("{"):
            try:
                answer = json.loads(text)
            except ValueError:
                answer = None  # caught mid-write
            if isinstance(answer, dict) and answer.get("request") == seq:
                return text
        if time.monotonic() >= deadline:
            raise AgentError(_no_answer(adb))
        time.sleep(POLL_S)


def _no_answer(adb: AgentDevice) -> str:
    """The agent reports through logcat, so quote it when the answer never arrived."""
    try:
        lines = _text(adb.exec_out("logcat -d -s alayout:V")).strip().splitlines()
    except AdbError:
        lines = []
    said = "; ".join(line.split(": ", 1)[-1].strip() for line in lines[-3:])
    return ("the agent attached to the app but never answered"
            + (f": {said}" if said else "; look at `adb logcat -s alayout`"))


def finish(adb: AgentDevice, session: "Session") -> None:
    """Let the agent go and reopen the inspected screen it was restarted into."""
    _send(adb, session.package, "exit")
    session.attached.release()  # the app process may die with the instrumentation
    if session.activity:
        try:
            launch(adb, session.package, session.activity)  # leave the screen as it was
        except AdbError:
            pass


class Session(NamedTuple):
    """What `collect` found: the agent's JSON, the app it came from, the running instrument call."""

    dump: str
    package: str
    activity: str | None
    attached: Attachment


def collect(adb: AgentDevice, wait: Callable[[str, str | None], None],
            log: Callable[[str], None] = lambda message: None,
            agent_timeout: float = DEFAULT_AGENT_TIMEOUT, answer_timeout: float = 90.0,
            cache: Path | None = None, debug: bool = False,
            signing: SigningKey | None = None) -> Session:
    """Restart the foreground app with the agent attached, call `wait(package, activity)` while the
    user opens the window to inspect, and return the agent's JSON with what it describes.

    The agent is left running on purpose: the caller captures the screen first and only then calls
    `finish`, because finishing takes the app's process down with it.
    """
    package, activity = foreground(adb)
    if not package:
        raise AgentError("no app on the screen to attach to; open the app on the device first")
    if not is_debuggable(adb, package):
        raise AgentError(f"{package} is not a debuggable build, so nothing can be injected into it;"
                         " capture the debug variant of the app to get composable names")
    cache = cache or cache_dir()
    attached = _attach(adb, package, activity, cache, getattr(adb, "adb_path", None),
                       agent_timeout, log, debug, signing)
    try:
        _prepare(adb, package, log, answer_timeout)
        wait(package, activity)
        seq = request(adb, package, "dump")
        dump = read_dump(adb, package, seq, answer_timeout)
    except (Exception, KeyboardInterrupt):  # an abandoned capture must not leave a polling agent
        _send(adb, package, "exit")
        attached.release()
        raise
    return Session(dump, package, activity, attached)


def _attach(adb: AgentDevice, package: str, activity: str | None, cache: Path,
            adb_path: Path | None, agent_timeout: float,
            log: Callable[[str], None], debug: bool = False,
            signing: SigningKey | None = None) -> Attachment:
    """Restart the app with an agent the device accepts: which key that is depends on the app.

    The agent APK has to be signed with the same key as the app on most devices, so every candidate
    key is built and tried in turn until one of them gets in. An explicit `signing` key (CLI flags)
    is the only one tried.
    """
    refused: list[str] = []
    for key in signing_keys(cache, override=signing):
        apk = build_agent(package, key=key, adb_path=adb_path, cache=cache)
        try:
            attached = start(adb, package, activity, apk, agent_timeout, debug)
        except SignatureRefused as e:
            refused.append(f"'{key.name}': {e}")
            log(f"the agent signed with {key.name} was not allowed into {package}, trying another")
            continue
        log(f"agent attached to {package} (signed with {key.name})")
        return attached
    raise AgentError(f"{package} refused every agent we could sign: " + "; ".join(refused)
                     + ". " + KEY_HINT)
