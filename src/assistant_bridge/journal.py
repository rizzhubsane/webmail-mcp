from __future__ import annotations

import json
from pathlib import Path

from assistant_bridge.audit import scrub
from assistant_bridge.paths import secure_file
from assistant_bridge.util import new_id, utcnow


class Journal:
    """Append-only log of assistant actions. Replies and sends keep enough to revisit."""

    def __init__(self, home: Path):
        self.path = home / "actions.jsonl"

    def record(self, **fields) -> dict:
        action = {
            "id": fields.get("id") or new_id("act"),
            "ts": utcnow(),
            "kind": fields.get("kind") or "tool",
            "tool": fields.get("tool") or "",
            "ok": bool(fields.get("ok", True)),
            "token_id": fields.get("token_id"),
            "message_id": fields.get("message_id"),
            "draft_id": fields.get("draft_id"),
            "sent_id": fields.get("sent_id"),
            "to": list(fields.get("to") or []),
            "subject": fields.get("subject") or "",
            "status": fields.get("status") or "",
            "summary": fields.get("summary") or "",
            "body": (fields.get("body") or "")[:1500],
            "sent_proof": fields.get("sent_proof") or "",
        }
        action = scrub(action)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
            secure_file(self.path)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(action, ensure_ascii=False) + "\n")
        return action

    def read_recent(self, limit: int = 50, kind: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows.append(json.loads(line))
        if kind:
            rows = [row for row in rows if row.get("kind") == kind or row.get("tool") == kind]
        capped = max(1, min(limit, 200))
        return rows[-capped:]

    def get(self, action_id: str) -> dict | None:
        for row in reversed(self.read_recent(200)):
            if row.get("id") == action_id:
                return row
        if not self.path.exists():
            return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if action_id in line:
                row = json.loads(line)
                if row.get("id") == action_id:
                    return row
        return None
