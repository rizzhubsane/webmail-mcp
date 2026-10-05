from __future__ import annotations

from pathlib import Path

from assistant_bridge.crypto import load_fernet
from assistant_bridge.store import JsonStore

_DEFAULT = {"password": None}


class Vault:
    """Fernet-encrypted connector password. The JSON file stores ciphertext only."""

    def __init__(self, home: Path):
        self.home = home
        self._store = JsonStore(home / "vault.json", _DEFAULT)

    def store_password(self, password: str) -> None:
        token = load_fernet(self.home).encrypt(password.encode()).decode()
        self._store.write({"password": token})

    def load_password(self) -> str | None:
        raw = self._store.read().get("password")
        if not raw:
            return None
        return load_fernet(self.home).decrypt(raw.encode()).decode()

    def has_password(self) -> bool:
        return bool(self._store.read().get("password"))

    def clear_password(self) -> None:
        self._store.write({"password": None})
