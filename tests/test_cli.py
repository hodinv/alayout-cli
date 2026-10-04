from helpers import FakeAdb, views_raw, views_responses, views_snapshot
from typer.testing import CliRunner

from layoutcli import cli
from layoutcli.adb import AdbError
from layoutcli.snapshot_io import save_capture

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
        def __init__(self, snapshot):
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
    monkeypatch.setattr(cli, "LayoutApp", lambda snap: type("A", (), {"run": lambda self: opened.append(snap)})())
    result = runner.invoke(cli.app, ["inspect"])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1
    assert list((tmp_path / "layout-snapshots").iterdir())


def test_inspect_rejects_non_snapshot_dir(tmp_path):
    result = runner.invoke(cli.app, ["inspect", str(tmp_path)])
    assert result.exit_code == 1
    assert "not a layoutcli snapshot" in result.output


def _record_app(monkeypatch):
    opened = []
    monkeypatch.setattr(cli, "LayoutApp", lambda snap: type("A", (), {"run": lambda self: opened.append(snap)})())
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
