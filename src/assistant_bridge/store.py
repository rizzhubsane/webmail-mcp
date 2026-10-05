from __future__ import annotations

import json
import threading
from pathlib import Path

from assistant_bridge.paths import secure_file

_LOCKS: dict[str, threading.Lock] = {}


def _lock(path: Path) -> threading.Lock:
    key = str(path)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = threading.Lock()
        _LOCKS[key] = lock
    return lock


class JsonStore:
    def __init__(self, path: Path, default: dict):
        self.path = path
        self.default = default

    def read(self) -> dict:
        with _lock(self.path):
            if not self.path.exists():
                return json.loads(json.dumps(self.default))
            return json.loads(self.path.read_text(encoding="utf-8"))

    def write(self, data: dict) -> None:
        payload = json.dumps(data, indent=2) + "\n"
        with _lock(self.path):
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            secure_file(tmp)
            tmp.replace(self.path)
            secure_file(self.path)
