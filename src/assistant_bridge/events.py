from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

from assistant_bridge.summarizer import extract_deadlines

IST = timezone(timedelta(hours=5, minutes=30))
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_DATE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})\s+(jan|feb|mar|march|apr|april|may|jun|june|jul|july|aug|sep|sept|oct|nov|dec)[a-z]*(?:\s+(\d{4}))?",
    re.I,
)
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_ROOM = re.compile(r"\b(?:LH|WS|MS|LT)\s*-?\s*\d+[A-Za-z]?\b|\bDogra Hall\b", re.I)
_URL = re.compile(r"https?://[^\s<>\"]+")
_PREFIX = re.compile(r"^(?:(?:re|fw|fwd)\s*:\s*)+", re.I)


def normalize_subject(subject: str) -> str:
    cleaned = _PREFIX.sub("", subject or "").strip()
    return re.sub(r"\s+", " ", cleaned)


def parse_loose_date(text: str, today: date | None = None) -> date | None:
    match = _DATE.search(text or "")
    if not match:
        return None
    if match.group(1):
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    month = _MONTHS[match.group(5).lower()[:3]]
    year = int(match.group(6) or (today or date.today()).year)
    return date(year, month, int(match.group(4)))


def extract_event(subject: str, body: str, *, today: date | None = None) -> dict:
    text = f"{subject or ''}\n{body or ''}"
    when = _first_date(text, today)
    clock = _TIME.search(text)
    start = None
    all_day = True
    if when and clock:
        hour = int(clock.group(1)) % 12
        if clock.group(3).lower() == "pm":
            hour += 12
        minute = int(clock.group(2) or 0)
        start = datetime.combine(when, time(hour, minute), IST)
        all_day = False
    elif when:
        start = datetime.combine(when, time(0, 0), IST)
    room = _ROOM.search(text)
    link = _URL.search(text)
    title = normalize_subject(subject) or "Campus event"
    return {
        "title": title,
        "start": start.isoformat() if start else None,
        "all_day": all_day if start else True,
        "location": room.group(0).replace("  ", " ") if room else "",
        "url": link.group(0).rstrip(").,") if link else "",
        "deadlines": extract_deadlines(text),
    }


def write_ics(event: dict) -> str:
    start = datetime.fromisoformat(event["start"])
    end = start + (timedelta(days=1) if event.get("all_day") else timedelta(hours=1))
    if event.get("all_day"):
        start_line = f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}"
        end_line = f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}"
    else:
        start_line = "DTSTART:" + _utc_stamp(start)
        end_line = "DTEND:" + _utc_stamp(end)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//webmail-mcp//EN",
        "BEGIN:VEVENT",
        f"UID:{event['id']}@webmail-mcp",
        start_line,
        end_line,
        "SUMMARY:" + _ics_text(event.get("title") or "Event"),
        "LOCATION:" + _ics_text(event.get("location") or ""),
        "DESCRIPTION:" + _ics_text(event.get("notes") or event.get("url") or ""),
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return "\r\n".join(lines)


def google_template(event: dict) -> str:
    start = datetime.fromisoformat(event["start"])
    if event.get("all_day"):
        end = start + timedelta(days=1)
        dates = f"{start.strftime('%Y%m%d')}/{end.strftime('%Y%m%d')}"
    else:
        end = start + timedelta(hours=1)
        dates = _utc_stamp(start) + "/" + _utc_stamp(end)
    details = event.get("notes") or ""
    if event.get("url"):
        details = (details + "\n" + event["url"]).strip()
    query = "&".join(
        [
            "action=TEMPLATE",
            "text=" + quote(event.get("title") or "Event"),
            "dates=" + dates,
            "location=" + quote(event.get("location") or ""),
            "details=" + quote(details),
        ]
    )
    return "https://calendar.google.com/calendar/render?" + query


def add_mac_event(event: dict) -> str:
    """Add the event to Calendar.app. A Google account on that calendar syncs it."""
    import subprocess

    start = datetime.fromisoformat(event["start"])
    end = start + (timedelta(hours=1) if not event.get("all_day") else timedelta(hours=1))
    if event.get("all_day"):
        end = start.replace(hour=18, minute=0)
        start = start.replace(hour=9, minute=0)
    script = "\n".join(
        [
            'tell application "Calendar"',
            'tell calendar "Calendar"',
            "set startDate to current date",
            f"set year of startDate to {start.year}",
            f"set month of startDate to {start.month}",
            f"set day of startDate to {start.day}",
            f"set hours of startDate to {start.hour}",
            f"set minutes of startDate to {start.minute}",
            "set seconds of startDate to 0",
            "set endDate to current date",
            f"set year of endDate to {end.year}",
            f"set month of endDate to {end.month}",
            f"set day of endDate to {end.day}",
            f"set hours of endDate to {end.hour}",
            f"set minutes of endDate to {end.minute}",
            "set seconds of endDate to 0",
            "make new event at end with properties {summary:"
            + _apple(event.get("title") or "Event")
            + ", start date:startDate, end date:endDate, location:"
            + _apple(event.get("location") or "")
            + ", description:"
            + _apple(event.get("notes") or event.get("url") or "")
            + "}",
            "end tell",
            "end tell",
        ]
    )
    completed = subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True, timeout=20)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "calendar app declined").strip()
        raise RuntimeError(detail[:240])
    return "Calendar"


def attachment_text(filename: str, content_type: str, data: bytes) -> str:
    kind = (content_type or "").lower()
    name = (filename or "").lower()
    if kind == "application/pdf" or name.endswith(".pdf"):
        return _pdf_text(data)
    if kind.startswith("text/") or name.endswith((".txt", ".csv", ".md", ".html", ".ics")):
        return data.decode("utf-8", errors="replace")[:20000]
    return ""


def _pdf_text(data: bytes) -> str:
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(part for part in parts if part).strip()[:20000]


def _first_date(text: str, today: date | None) -> date | None:
    for deadline in extract_deadlines(text):
        parsed = parse_loose_date(deadline, today)
        if parsed:
            return parsed
    return parse_loose_date(text, today)


def _utc_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ics_text(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _apple(value: str) -> str:
    text = "".join(char for char in (value or "") if char >= " " and char != "\x7f")
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + text[:240] + '"'
