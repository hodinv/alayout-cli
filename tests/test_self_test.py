from pathlib import Path

from typer.testing import CliRunner

from alayout import __version__, cli

SAMPLE = Path(__file__).parent / "fixtures" / "snapshot"
runner = CliRunner()


def test_version():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"alayout {__version__}"


def test_self_test_on_sample_snapshot():
    result = runner.invoke(cli.app, ["--self-test", str(SAMPLE)])
    assert result.exit_code == 0, result.output
    assert f"alayout {__version__} self-test: OK" in result.output
    assert "screenshot 108x240" in result.output


def test_self_test_on_missing_snapshot_fails(tmp_path):
    result = runner.invoke(cli.app, ["--self-test", str(tmp_path)])
    assert result.exit_code == 1
    assert "self-test: FAILED" in result.output
