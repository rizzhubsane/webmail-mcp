from cryptography.fernet import Fernet

from assistant_bridge.service import Bridge
from assistant_bridge.vault import Vault


def test_vault_encrypts_password(home):
    secret = "mailbox-password-should-not-appear"
    vault = Vault(home)
    vault.store_password(secret)

    stored = (home / "vault.json").read_text(encoding="utf-8")
    assert secret not in stored
    assert vault.load_password() == secret
    assert (home / "master.key").stat().st_mode & 0o777 == 0o600
    assert home.stat().st_mode & 0o777 == 0o700


def test_env_master_key_is_not_written(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ASSISTANT_BRIDGE_MASTER_KEY", key)
    home = tmp_path / "env-home"
    Bridge.init(home)
    assert not (home / "master.key").exists()

    bridge = Bridge.open(home)
    bridge.vault.store_password("env-only-secret")
    assert "env-only-secret" not in (home / "vault.json").read_text(encoding="utf-8")
    assert bridge.vault.load_password() == "env-only-secret"
