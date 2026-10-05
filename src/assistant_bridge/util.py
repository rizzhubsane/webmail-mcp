from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def clean_header(value: str, label: str) -> str:
    text = str(value or "")
    if any(char in text for char in "\r\n\x00"):
        from assistant_bridge.errors import BridgeError

        raise BridgeError(f"{label} contains a line break")
    return text.strip()


def parse_recipients(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,;]", value)
    else:
        parts = list(value)
    found: list[str] = []
    for part in parts:
        item = clean_header(str(part), "recipient")
        if item and item not in found:
            found.append(item)
    if not found:
        from assistant_bridge.errors import BridgeError

        raise BridgeError("at least one recipient is required")
    return found


def clean_folder(name: str | None) -> str:
    from assistant_bridge.errors import BridgeError

    cleaned = (name or "INBOX").strip() or "INBOX"
    if len(cleaned) > 200 or any(char in cleaned for char in "\r\n\x00"):
        raise BridgeError("invalid folder")
    return cleaned


def bound_limit(value, default: int = 20, cap: int = 40) -> int:
    from assistant_bridge.errors import BridgeError

    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise BridgeError("limit must be an integer") from exc
    if number < 1:
        raise BridgeError("limit must be at least 1")
    return min(number, cap)
