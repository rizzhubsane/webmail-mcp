from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\[[A-Za-z][^\]]{0,40}\]")
_EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?:\+?\d[\d\-\s().]{8,}\d)")
_PROMISES = (
    re.compile(r"\bi guarantee\b", re.I),
    re.compile(r"\bi promise\b", re.I),
    re.compile(r"\bguarantee that\b", re.I),
    re.compile(r"\bdefinitely will\b", re.I),
    re.compile(r"\b100\s*%\b", re.I),
    re.compile(r"\bno problem at all\b", re.I),
    re.compile(r"\byou have my word\b", re.I),
)
_SLANG = {
    "lol",
    "lmao",
    "lmfao",
    "gonna",
    "wanna",
    "kinda",
    "btw",
    "idk",
    "omg",
    "thx",
    "pls",
    "plz",
    "ur",
    "nah",
    "dude",
}
_STOP = {
    "that",
    "this",
    "with",
    "from",
    "your",
    "you",
    "please",
    "could",
    "would",
    "should",
    "about",
    "whether",
    "there",
    "their",
    "have",
    "will",
    "also",
    "just",
    "into",
    "them",
    "they",
    "what",
    "when",
    "where",
    "which",
    "while",
    "before",
    "after",
    "email",
    "mail",
    "thanks",
    "thank",
    "hello",
    "please",
}
_MIN_CHARS = 40


def check_draft(text: str, source_text: str | None = None) -> dict:
    body = text or ""
    stripped = body.strip()
    blocking: list[str] = []
    warnings: list[str] = []

    if len(stripped) < _MIN_CHARS:
        blocking.append("too short")
    placeholders = _PLACEHOLDER.findall(body)
    if placeholders:
        blocking.append("leftover placeholders: " + ", ".join(dict.fromkeys(placeholders)))

    words = re.findall(r"[A-Za-z']+", body.lower())
    slang = [word for word in dict.fromkeys(words) if word in _SLANG]
    if slang:
        warnings.append("slang: " + ", ".join(slang))
    if any(pattern.search(body) for pattern in _PROMISES):
        warnings.append("hard promise")
    if _EMAIL.search(body):
        warnings.append("email address in body")
    if _has_phone(body):
        warnings.append("phone number in body")

    for ask in _unanswered(stripped, source_text):
        warnings.append("unanswered ask: " + ask)

    return {"ok": not blocking, "blocking": blocking, "warnings": warnings}


def _has_phone(text: str) -> bool:
    for match in _PHONE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 10:
            return True
    return False


def _unanswered(draft: str, source_text: str | None) -> list[str]:
    if not source_text:
        return []
    from assistant_bridge.summarizer import summarize_text

    draft_words = set(re.findall(r"[a-z]{4,}", draft.lower()))
    missing: list[str] = []
    for ask in summarize_text("", source_text)["asks"]:
        content = [
            word
            for word in re.findall(r"[a-z]{4,}", ask.lower())
            if word not in _STOP
        ]
        if content and not any(word in draft_words for word in content):
            compact = re.sub(r"\s+", " ", ask).strip()
            missing.append(compact[:140])
    return missing
