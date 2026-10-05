from __future__ import annotations

import re

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_DATE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+(?:jan|feb|mar|march|apr|april|may|jun|june|jul|july|aug|sep|sept|oct|nov|dec)[a-z]*(?:\s+\d{4})?"
    r"|(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r")\b",
    re.I,
)
_CUE = re.compile(
    r"\b(?:deadline(?:\s+is)?|due(?:\s+(?:by|on))?|no later than|before|until|by)\b(.{0,60})",
    re.I,
)
_ASK_PREFIX = re.compile(r"^(please|could you|can you|would you|kindly)\b", re.I)
_GREETING = re.compile(r"^(hi|hello|hey|dear|thanks|thank you|best|regards|cheers)\b", re.I)


def summarize_text(subject: str, body: str) -> dict:
    text = (body or "").strip()
    sentences = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    headline = (subject or "").strip() or (sentences[0] if sentences else "Message")
    bullets = []
    for sentence in sentences:
        if _GREETING.match(sentence):
            continue
        if len(sentence) < 12:
            continue
        bullets.append(sentence)
        if len(bullets) == 4:
            break
    asks = [sentence for sentence in sentences if "?" in sentence or _ASK_PREFIX.match(sentence)]
    deadlines = extract_deadlines(f"{subject}\n{text}")
    if asks and deadlines:
        action = "Reply with the requested information before the deadline."
    elif asks:
        action = "Reply to the questions in this message."
    elif deadlines:
        action = "Note the deadline and act before it."
    else:
        action = "No reply required unless you want to follow up."
    return {
        "headline": headline,
        "bullets": bullets,
        "asks": asks,
        "deadlines": deadlines,
        "suggested_action": action,
    }


def extract_deadlines(text: str) -> list[str]:
    # "by video" is a medium, not a due date. "by the way" is not one either.
    scrubbed = re.sub(r"\bby\s+video\b", "via recording", text or "", flags=re.I)
    scrubbed = re.sub(r"\bby\s+the\s+way\b", "incidentally", scrubbed, flags=re.I)
    found: list[str] = []
    for match in _CUE.finditer(scrubbed):
        window = match.group(1)
        if re.match(r"\s*video\b", window, flags=re.I):
            continue
        date = _DATE.search(window)
        if not date:
            continue
        value = date.group(0).strip()
        if value.lower() == "video":
            continue
        if value not in found:
            found.append(value)
    return found
