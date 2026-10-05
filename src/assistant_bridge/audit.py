from __future__ import annotations

import json
import re
from pathlib import Path

from assistant_bridge.util import utcnow

_SECRET_KEYS = {
    "password",
    "token",
    "phrase",
    "confirm_code",
    "authorization",
    "master_key",
    "raw_token",
    "command_phrase",
    "hash",
}


def redact_log(text: str) -> str:
    cleaned = re.sub(r"(token=)[^&\s\"']+", r"\1[redacted]", text or "")
    return re.sub(r"ab_[A-Za-z0-9_-]+", "[redacted-token]", cleaned)


def scrub(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = scrub(item)
        return cleaned
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str) and value.startswith("ab_"):
        return "[redacted-token]"
    return value


class AuditLog:
    def __init__(self, home: Path):
        self.path = home / "audit.jsonl"

    def record(self, action: str, *, ok: bool, token_id: str | None = None, detail: dict | None = None) -> None:
        event = {
            "ts": utcnow(),
            "action": action,
            "ok": bool(ok),
            "token_id": token_id,
            "detail": scrub(detail or {}),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_recent(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        capped = max(1, min(limit, 200))
        return [json.loads(line) for line in lines[-capped:]]
