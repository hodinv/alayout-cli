import pytest


@pytest.fixture(autouse=True)
def _clear_alayout_env(monkeypatch):
    """Tests assert exact labels/output, so they must not depend on the developer's shell. A test
    that wants a flag set (e.g. the debug markers) sets it itself with monkeypatch after this runs."""
    monkeypatch.delenv("ALAYOUT_DEBUG", raising=False)
