from __future__ import annotations

from assistant_bridge.connectors.imap_conn import build_outgoing
from assistant_bridge.errors import BridgeError
from assistant_bridge.util import bound_limit, clean_folder

DEMO_MESSAGES = [
    {
        "id": "demo-course",
        "from": "prof.sharma@example.edu",
        "to": ["student@example.edu"],
        "subject": "COL106 assignment deadline",
        "date": "2026-03-20T09:00:00+00:00",
        "body": (
            "Hello class.\n\n"
            "The COL106 assignment 3 is due by 28 March 2026, 11:59 PM. "
            "Please submit the PDF on the course portal.\n\n"
            "Could you also confirm whether you will take the optional viva?\n\n"
            "Office hours remain by video this week; that is not a deadline.\n\n"
            "Thanks,\nProf Sharma"
        ),
    },
    {
        "id": "demo-intern",
        "from": "recruiter@example.com",
        "to": ["student@example.edu"],
        "subject": "Internship interview slots",
        "date": "2026-03-22T11:30:00+00:00",
        "body": (
            "Hi.\n\n"
            "We are scheduling summer internship interviews. "
            "Please pick a slot: 2 April 2026 at 10:00 or 3 April 2026 at 15:00.\n\n"
            "Could you reply with your preferred slot by 30 March 2026?\n\n"
            "Best,\nCampus Recruiting"
        ),
    },
    {
        "id": "demo-poster",
        "from": "club-lead@example.edu",
        "to": ["student@example.edu"],
        "subject": "Please review the club poster",
        "date": "2026-03-25T16:00:00+00:00",
        "body": (
            "Hey.\n\n"
            "Please review the club poster draft and tell me if the date and venue look right. "
            "We need comments before Friday.\n\n"
            "Thanks!"
        ),
    },
    {
        "id": "demo-news",
        "from": "news@example.edu",
        "to": ["student@example.edu"],
        "subject": "Weekly campus newsletter",
        "date": "2026-04-01T08:00:00+00:00",
        "body": (
            "This week on campus: library hours, a seminar on Friday, and a reminder that "
            "the newsletter is informational only.\n\n"
            "No action is required."
        ),
    },
]


class MockConnector:
    def __init__(self, read_state):
        self._read_state = read_state
        self.label = "mock"

    def _all(self) -> list[dict]:
        extra = self._read_state().get("extra_inbox") or []
        return [dict(item) for item in (*DEMO_MESSAGES, *extra)]

    def list_inbox(self, limit: int = 20, folder: str = "INBOX") -> list[dict]:
        if clean_folder(folder).upper() != "INBOX":
            return []
        rows = self._all()
        rows.sort(key=lambda item: item.get("date") or "", reverse=True)
        return rows[: bound_limit(limit)]

    def fetch(self, message_id: str, folder: str = "INBOX") -> dict | None:
        if clean_folder(folder).upper() != "INBOX":
            return None
        for item in self._all():
            if item["id"] == message_id:
                return item
        return None

    def search(self, *, text: str | None = None, sender: str | None = None, subject: str | None = None, limit: int = 20, folder: str = "INBOX") -> list[dict]:
        if not any([text, sender, subject]):
            raise BridgeError("provide text, from, or subject")
        if clean_folder(folder).upper() != "INBOX":
            return []
        rows = []
        for item in self._all():
            if sender and sender.lower() not in item["from"].lower():
                continue
            if subject and subject.lower() not in item["subject"].lower():
                continue
            if text:
                blob = f"{item['from']}\n{item['subject']}\n{item['body']}".lower()
                if text.lower() not in blob:
                    continue
            rows.append(item)
        rows.sort(key=lambda item: item.get("date") or "", reverse=True)
        return rows[: bound_limit(limit)]

    def list_folders(self) -> list[str]:
        return ["INBOX"]

    def send(self, *, to: list[str], subject: str, body: str, from_addr: str, attachments: list[dict] | None = None) -> dict:
        message = build_outgoing(
            to=to,
            subject=subject,
            body=body,
            from_addr=from_addr or "student@example.edu",
            attachments=attachments,
        )
        return {
            "via": "mock",
            "from": from_addr or "student@example.edu",
            "to": list(to),
            "subject": subject,
            "message_id": str(message["Message-ID"]),
            "sent_proof": "found",
            "sent_folder": "Sent",
            "attachments": [item["filename"] for item in attachments or []],
        }

    def read_attachment(self, message_id: str, filename: str, folder: str = "INBOX") -> dict:
        item = self.fetch(message_id, folder)
        if item is None:
            raise BridgeError("message not found", status=404)
        wanted = (filename or "").strip().lower()
        for attachment in item.get("attachments") or []:
            if (attachment.get("filename") or "").lower() == wanted:
                data = attachment.get("data")
                if isinstance(data, str):
                    data = data.encode()
                return {
                    "filename": attachment["filename"],
                    "content_type": attachment.get("content_type") or "application/octet-stream",
                    "size": attachment.get("size") or len(data or b""),
                    "data": data if isinstance(data, bytes) else (attachment.get("text") or "").encode(),
                    "text": attachment.get("text"),
                }
        raise BridgeError("attachment not found", status=404)

    def probe(self) -> dict:
        return {
            "ok": True,
            "connector": "mock",
            "inbox_count": len(self.list_inbox()),
            "detail": "demo inbox; nothing is sent off this machine",
        }
