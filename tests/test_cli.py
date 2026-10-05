import os
import re
import subprocess
import sys

from helpers import FakeAdb, views_raw, views_responses, views_snapshot
from typer.testing import CliRunner

from alayout import cli
from alayout.adb import AdbError
from alayout.apk import ApkError, ApkIndex
from alayout.snapshot_io import load_apk_index, save_capture

runner = CliRunner()


def test_capture_writes_snapshot_and_prints_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    out = tmp_path / "snap"
    result = runner.invoke(cli.app, ["capture", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "snapshot.json").is_file()
    assert "18 views" in result.output
    assert "com.example.demo.MainActivity" in result.output


def test_capture_reports_adb_error(monkeypatch):
    def boom(adb, serial):
        raise AdbError("no Android device connected")
    monkeypatch.setattr(cli, "_make_adb", boom)
    result = runner.invoke(cli.app, ["capture"])
    assert result.exit_code == 1
    assert "no Android device connected" in result.output


def test_inspect_opens_saved_snapshot(tmp_path, monkeypatch):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    opened = []

    class FakeApp:
        def __init__(self, snapshot, base_dir=None):
            opened.append(snapshot)

        def run(self):
            pass

    monkeypatch.setattr(cli, "LayoutApp", FakeApp)
    result = runner.invoke(cli.app, ["inspect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert opened[0].package == "com.example.demo"


def test_inspect_without_dir_captures_first(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    opened = []
    monkeypatch.setattr(cli, "LayoutApp", lambda snap, base_dir=None: type("A", (), {"run": lambda self: opened.append(snap)})())
    result = runner.invoke(cli.app, ["inspect"])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1
    assert list((tmp_path / "layout-snapshots").iterdir())


def test_inspect_rejects_non_snapshot_dir(tmp_path):
    result = runner.invoke(cli.app, ["inspect", str(tmp_path)])
    assert result.exit_code == 1
    assert "not an alayout snapshot" in result.output


def _record_app(monkeypatch):
    opened = []
    monkeypatch.setattr(cli, "LayoutApp", lambda snap, base_dir=None: type("A", (), {"run": lambda self: opened.append(snap)})())
    return opened


def test_no_arguments_captures_then_inspects(tmp_path, monkeypatch):
    import re
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    opened = _record_app(monkeypatch)
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1 and opened[0].package == "com.example.demo"
    (folder,) = list((tmp_path / "layout-snapshots").iterdir())
    assert re.fullmatch(r"capture-\d{8}-\d{6}", folder.name)
    assert (folder / "snapshot.json").is_file()


def test_no_command_accepts_serial_and_adb(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = []
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: seen.append((adb, serial)) or FakeAdb(views_responses()))
    _record_app(monkeypatch)
    result = runner.invoke(cli.app, ["-s", "emu", "--adb", "C:/sdk/adb.exe"])
    assert result.exit_code == 0, result.output
    assert seen == [("C:/sdk/adb.exe", "emu")]


def test_help_still_lists_commands():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "capture" in result.output and "inspect" in result.output


def test_clean_uninstalls_the_agent_when_present(monkeypatch):
    adb = FakeAdb({"pm list packages com.alayout.agent": b"package:com.alayout.agent\n"})
    monkeypatch.setattr(cli, "_make_adb", lambda a, s: adb)
    result = runner.invoke(cli.app, ["clean"])
    assert result.exit_code == 0
    assert adb.uninstalled == ["com.alayout.agent"]
    assert "removed com.alayout.agent" in result.output


def test_clean_is_quiet_when_agent_absent(monkeypatch):
    adb = FakeAdb({"pm list packages com.alayout.agent": b""})
    monkeypatch.setattr(cli, "_make_adb", lambda a, s: adb)
    result = runner.invoke(cli.app, ["clean"])
    assert result.exit_code == 0
    assert adb.uninstalled == []
    assert "was not installed" in result.output


def test_compose_flag_reaches_capture(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_capture",
                        lambda adb, serial, out, apk=None, compose=False, signing=None:
                        seen.update(compose=compose, signing=signing) or tmp_path)
    result = runner.invoke(cli.app, ["capture", "--compose"])
    assert result.exit_code == 0
    assert seen["compose"] is True and seen["signing"] is None


def test_alayout_debug_env_enables_agent_debug(tmp_path, monkeypatch):
    seen = {}

    class FakeSession:
        dump = "{}"

    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    monkeypatch.setattr(cli, "collect_names",
                        lambda adb, wait, log, signing=None, debug=False:
                        seen.update(debug=debug) or FakeSession())
    monkeypatch.setattr(cli, "finish_names", lambda adb, session: None)
    monkeypatch.setenv("ALAYOUT_DEBUG", "1")
    result = runner.invoke(cli.app, ["capture", "--compose", "-o", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert seen["debug"] is True


def test_compose_without_debug_env_stays_quiet(tmp_path, monkeypatch):
    seen = {}

    class FakeSession:
        dump = "{}"

    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    monkeypatch.setattr(cli, "collect_names",
                        lambda adb, wait, log, signing=None, debug=False:
                        seen.update(debug=debug) or FakeSession())
    monkeypatch.setattr(cli, "finish_names", lambda adb, session: None)
    monkeypatch.delenv("ALAYOUT_DEBUG", raising=False)
    result = runner.invoke(cli.app, ["capture", "--compose", "-o", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert seen["debug"] is False


def test_keystore_flags_build_a_signing_override(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_capture",
                        lambda adb, serial, out, apk=None, compose=False, signing=None:
                        seen.update(signing=signing) or tmp_path)
    result = runner.invoke(cli.app, ["capture", "--compose", "--keystore", "my.ks",
                                     "--key-alias", "rel", "--key-password", "pw"])
    assert result.exit_code == 0
    key = seen["signing"]
    assert key is not None and key.alias == "rel"
    assert key.key_password == "pw" and key.store_password == "pw"
    assert str(key.keystore) == "my.ks"


def _saved(tmp_path, name, captured_at, activity):
    snap = views_snapshot()
    snap.captured_at = captured_at
    snap.activity = activity
    save_capture(views_raw(), snap, tmp_path / "layout-snapshots" / name)


def test_inspect_without_dir_lists_snapshots_newest_first_and_opens_choice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _saved(tmp_path, "capture-old", "2026-10-01T10:00:00+00:00", "com.a.Old")
    _saved(tmp_path, "capture-new", "2026-10-05T10:00:00+00:00", "com.a.New")
    opened = _record_app(monkeypatch)
    result = runner.invoke(cli.app, ["inspect"], input="2\n")
    assert result.exit_code == 0, result.output
    assert result.output.index("capture-new") < result.output.index("capture-old")
    assert opened[0].activity == "com.a.Old"


def test_inspect_picker_defaults_to_newest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _saved(tmp_path, "capture-old", "2026-10-01T10:00:00+00:00", "com.a.Old")
    _saved(tmp_path, "capture-new", "2026-10-05T10:00:00+00:00", "com.a.New")
    opened = _record_app(monkeypatch)
    result = runner.invoke(cli.app, ["inspect"], input="\n")
    assert result.exit_code == 0, result.output
    assert opened[0].activity == "com.a.New"


def test_inspect_picker_n_captures_new_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _saved(tmp_path, "capture-old", "2026-10-01T10:00:00+00:00", "com.a.Old")
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    opened = _record_app(monkeypatch)
    result = runner.invoke(cli.app, ["inspect"], input="n\n")
    assert result.exit_code == 0, result.output
    assert opened[0].activity == "com.example.demo.MainActivity"
    assert len(list((tmp_path / "layout-snapshots").iterdir())) == 2


def test_inspect_picker_reasks_on_invalid_choice(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _saved(tmp_path, "capture-old", "2026-10-01T10:00:00+00:00", "com.a.Old")
    opened = _record_app(monkeypatch)
    result = runner.invoke(cli.app, ["inspect"], input="9\nx\n1\n")
    assert result.exit_code == 0, result.output
    assert opened[0].activity == "com.a.Old"


def test_inspect_picker_skips_unreadable_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _saved(tmp_path, "capture-ok", "2026-10-01T10:00:00+00:00", "com.a.Ok")
    (tmp_path / "layout-snapshots" / "junk").mkdir()
    (tmp_path / "layout-snapshots" / "broken").mkdir()
    (tmp_path / "layout-snapshots" / "broken" / "snapshot.json").write_text("{", encoding="utf-8")
    opened = _record_app(monkeypatch)
    result = runner.invoke(cli.app, ["inspect"], input="1\n")
    assert result.exit_code == 0, result.output
    assert "junk" not in result.output and "broken" not in result.output
    assert opened[0].activity == "com.a.Ok"


def test_check_prints_issues(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    result = runner.invoke(cli.app, ["check", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "2 warnings, 2 info" in result.output
    assert "touch-target" in result.output and "#fab_small" in result.output


def test_check_output_redirected_to_file_does_not_crash(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "cp1252"
    proc = subprocess.run([sys.executable, "-c", "from alayout.cli import app; app()", "check", str(tmp_path)],
                          capture_output=True, env=env)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "⚠" in proc.stdout.decode("utf-8")


def test_diff_prints_changes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    save_capture(views_raw(), views_snapshot(), a)
    changed = views_snapshot()
    next(n for n, _ in changed.root.walk() if n.text == "Demo").text = "Settings"
    save_capture(views_raw(), changed, b)
    result = runner.invoke(cli.app, ["diff", str(a), str(b)])
    assert result.exit_code == 0, result.output
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert '~ /LinearLayout/#content/#root/#toolbar/AppCompatTextView  text "Demo" -> "Settings"' in plain
    assert "0 added, 0 removed, 1 changed" in result.output


def test_diff_identical_snapshots(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path / "a")
    result = runner.invoke(cli.app, ["diff", str(tmp_path / "a"), str(tmp_path / "a")])
    assert result.exit_code == 0
    assert "no differences" in result.output


def test_diff_picks_missing_snapshots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _saved(tmp_path, "capture-old", "2026-10-01T10:00:00+00:00", "com.a.Old")
    _saved(tmp_path, "capture-new", "2026-10-05T10:00:00+00:00", "com.a.New")
    result = runner.invoke(cli.app, ["diff"], input="2\n1\n")
    assert result.exit_code == 0, result.output
    assert "First snapshot" in result.output and "Second snapshot" in result.output
    assert "capture-old" in result.output.splitlines()[-2] or "no differences" in result.output


def test_capture_with_local_apk_maps_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    monkeypatch.setattr(cli, "find_aapt2", lambda adb_path=None: tmp_path / "aapt2")
    monkeypatch.setattr(cli, "build_index", lambda apk, aapt2: ApkIndex(
        ids={"toolbar": ["res/layout/activity_main.xml"], "unused": ["res/layout/x.xml"],
             "content": ["res/layout/abc_popup_menu_item_layout.xml"]},
        layouts={"res/layout/activity_main.xml": "<A/>", "res/layout/x.xml": "<X/>",
                 "res/layout/abc_popup_menu_item_layout.xml": "<P/>"}))
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    out = tmp_path / "snap"
    result = runner.invoke(cli.app, ["capture", "-o", str(out), "--apk", str(apk)])
    assert result.exit_code == 0, result.output
    assert "ok (1 views mapped)" in result.output
    assert load_apk_index(out).to_dict() == {"ids": {"toolbar": ["res/layout/activity_main.xml"]},
                                             "layouts": {"res/layout/activity_main.xml": "<A/>"}}


def test_capture_apk_failure_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))

    def no_aapt2(adb_path=None):
        raise ApkError("aapt2 not found (install Android SDK build-tools)")
    monkeypatch.setattr(cli, "find_aapt2", no_aapt2)
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"PK")
    result = runner.invoke(cli.app, ["capture", "-o", str(tmp_path / "snap"), "--apk", str(apk)])
    assert result.exit_code == 0, result.output
    assert "aapt2 not found" in result.output
    assert load_apk_index(tmp_path / "snap") is None


def test_diff_warns_when_snapshots_come_from_different_sources(tmp_path):
    save_capture(views_raw(), views_snapshot(), tmp_path / "a")
    raw = views_raw()
    raw.dumpsys_text = None
    raw.errors["dumpsys"] = "boom"
    from alayout.build import build_snapshot
    save_capture(raw, build_snapshot(raw, "2026-10-05T10:00:00+00:00"), tmp_path / "b")
    result = runner.invoke(cli.app, ["diff", str(tmp_path / "a"), str(tmp_path / "b")])
    assert result.exit_code == 0, result.output
    assert "different sources" in result.output
    assert 'class "' not in result.output


def test_capture_summary_mentions_compose_components(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_make_adb", lambda adb, serial: FakeAdb(views_responses()))
    result = runner.invoke(cli.app, ["capture", "-o", str(tmp_path / "s")])
    assert result.exit_code == 0, result.output
    assert "Compose UI: 1 component" in result.output
