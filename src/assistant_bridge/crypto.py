from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from assistant_bridge.errors import BridgeError
from assistant_bridge.paths import secure_file


def generate_master_key() -> bytes:
    return Fernet.generate_key()


def load_fernet(home: Path) -> Fernet:
    env = os.environ.get("ASSISTANT_BRIDGE_MASTER_KEY")
    if env:
        key = env.strip().encode()
    else:
        path = home / "master.key"
        if not path.exists():
            raise BridgeError("master key missing; run webmail-mcp init", status=500)
        key = path.read_bytes().strip()
    try:
        return Fernet(key)
    except (ValueError, InvalidToken) as exc:
        raise BridgeError("invalid master key", status=500) from exc


def write_master_key(home: Path, key: bytes) -> Path:
    path = home / "master.key"
    path.write_bytes(key)
    secure_file(path)
    return path
