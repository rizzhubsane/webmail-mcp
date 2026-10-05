from __future__ import annotations

import os
from pathlib import Path

HOME_FILES = (
    "master.key",
    "vault.json",
    "tokens.json",
    "state.json",
    "config.json",
    "audit.jsonl",
)


def resolve_home(explicit: Path | str | None = None) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser()
    env = os.environ.get("ASSISTANT_BRIDGE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".assistant-bridge"


def config_dir() -> Path:
    packaged = Path(__file__).resolve().parents[2] / "config"
    if (packaged / "chatgpt.openapi.yaml").exists():
        return packaged
    cwd = Path.cwd() / "config"
    if (cwd / "chatgpt.openapi.yaml").exists():
        return cwd
    return packaged


def secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def secure_file(path: Path) -> None:
    if path.exists():
        path.chmod(0o600)
