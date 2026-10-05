import pytest

from assistant_bridge.service import Bridge


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "bridge-home"
    monkeypatch.setenv("ASSISTANT_BRIDGE_HOME", str(path))
    monkeypatch.delenv("ASSISTANT_BRIDGE_MASTER_KEY", raising=False)
    monkeypatch.delenv("ASSISTANT_BRIDGE_IMAP_PASSWORD", raising=False)
    monkeypatch.delenv("ASSISTANT_BRIDGE_TOKEN", raising=False)
    Bridge.init(path)
    return path


@pytest.fixture
def bridge(home):
    return Bridge.open(home)
