from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

from assistant_bridge.errors import BridgeError
from assistant_bridge.store import JsonStore
from assistant_bridge.util import new_id, utcnow

SCOPES = ("read", "draft", "send")
_DEFAULT = {"tokens": []}


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def hash_phrase(phrase: str) -> str:
    return hashlib.sha256(phrase.encode()).hexdigest()


class TokenStore:
    def __init__(self, home: Path):
        self._store = JsonStore(home / "tokens.json", _DEFAULT)

    def create(self, name: str, scopes: list[str]) -> tuple[str, dict]:
        cleaned = _clean_scopes(scopes)
        label = name.strip() or "token"
        raw = "ab_" + secrets.token_urlsafe(32)
        record = {
            "id": new_id("tok"),
            "name": label,
            "hash": hash_token(raw),
            "scopes": cleaned,
            "created_at": utcnow(),
            "revoked": False,
        }
        data = self._store.read()
        data["tokens"].append(record)
        self._store.write(data)
        return raw, {"id": record["id"], "name": label, "scopes": cleaned, "created_at": record["created_at"]}

    def public_list(self) -> list[dict]:
        listed = []
        for rec in self._store.read()["tokens"]:
            listed.append(
                {
                    "id": rec["id"],
                    "name": rec["name"],
                    "scopes": list(rec["scopes"]),
                    "created_at": rec["created_at"],
                    "revoked": bool(rec.get("revoked")),
                }
            )
        return listed

    def revoke(self, token_id: str) -> dict:
        data = self._store.read()
        for rec in data["tokens"]:
            if rec["id"] == token_id:
                rec["revoked"] = True
                self._store.write(data)
                return {"id": rec["id"], "revoked": True}
        raise BridgeError("token not found", status=404)

    def verify(self, raw: str | None) -> dict | None:
        if not raw or not raw.startswith("ab_"):
            return None
        digest = hash_token(raw)
        for rec in self._store.read()["tokens"]:
            if rec.get("revoked"):
                continue
            stored = rec.get("hash") or ""
            if len(stored) != len(digest):
                continue
            if hmac.compare_digest(stored, digest):
                return {"id": rec["id"], "name": rec["name"], "scopes": list(rec["scopes"])}
        return None


def _clean_scopes(scopes: list[str]) -> list[str]:
    if not scopes:
        raise BridgeError("at least one scope is required")
    unknown = [scope for scope in scopes if scope not in SCOPES]
    if unknown:
        raise BridgeError("unknown scope: " + ", ".join(unknown))
    cleaned: list[str] = []
    for scope in scopes:
        if scope not in cleaned:
            cleaned.append(scope)
    return cleaned


def require_scopes(record: dict, scopes: tuple[str, ...] | list[str], *, mode: str = "all") -> None:
    have = set(record.get("scopes") or [])
    needed = list(scopes)
    if mode == "any":
        ok = any(scope in have for scope in needed)
    else:
        ok = all(scope in have for scope in needed)
    if not ok:
        raise BridgeError("insufficient scope", status=403)
