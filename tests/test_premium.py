from datetime import date
from pathlib import Path

import pytest

from assistant_bridge.connectors.imap_conn import choose_sent_folders
from assistant_bridge.errors import BridgeError
from assistant_bridge.events import attachment_text

PAGE = """
<html><body>
Vangla, Prashanth [Ph.D.] Associate Professor Group: Geotechnical Engineering
E-mail: pvangla05[at]civil.iitd.ac.in
Sharma, Aditi Group: Structural Engineering E-mail: asharma[at]civil.iitd.ac.in
</body></html>
"""

LECTURE = (
    "Lecture 7 is on 7 Oct 2026 at 5:00 pm in LH 111. "
    "Join at https://example.edu/lecture7"
)


def test_directory_resolves_geotech_professor(bridge):
    bridge.ingest_directory_page(PAGE, "Civil Engineering")
    found = bridge.resolve_person("professor prashanth from geotech department")
    assert found["chosen"]["email"] == "pvangla05@civil.iitd.ac.in"
    assert "Geotech" in found["chosen"]["group"]
    other = bridge.resolve_person("aditi sharma structural")
    assert other["chosen"]["email"] == "asharma@civil.iitd.ac.in"


def test_thread_groups_replies(bridge):
    state = bridge.state.read()
    state["extra_inbox"] = [
        {
            "id": "lecture-1",
            "from": "office@civil.iitd.ac.in",
            "to": ["student@example.edu"],
            "subject": "Lecture 7",
            "date": "2026-10-01T09:00:00+00:00",
            "body": LECTURE,
        },
        {
            "id": "lecture-2",
            "from": "office@civil.iitd.ac.in",
            "to": ["student@example.edu"],
            "subject": "Re: Lecture 7",
            "date": "2026-10-02T09:00:00+00:00",
            "body": "Room confirmed.",
        },
    ]
    bridge.state.write(state)
    thread = bridge.get_thread("lecture-1")
    assert thread["subject"] == "Lecture 7"
    assert [item["id"] for item in thread["messages"]] == ["lecture-1", "lecture-2"]


def test_followups_surface_unanswered_mail_and_tomorrow(bridge):
    state = bridge.state.read()
    state["sent"] = [
        {
            "id": "sent_old",
            "to": ["office@civil.iitd.ac.in"],
            "subject": "Project form",
            "body": "Please sign this.",
            "sent_at": "2026-09-20T10:00:00+00:00",
        }
    ]
    state["extra_inbox"] = [
        {
            "id": "due-soon",
            "from": "office@civil.iitd.ac.in",
            "to": ["student@example.edu"],
            "subject": "Form reminder",
            "date": "2026-10-02T09:00:00+00:00",
            "body": "The form is due by 4 Oct 2026.",
        }
    ]
    bridge.state.write(state)
    found = bridge.list_followups(days=3, today=date(2026, 10, 3))
    assert found["waiting"][0]["sent_id"] == "sent_old"
    assert found["due_tomorrow"][0]["id"] == "due-soon"

    state = bridge.state.read()
    state["extra_inbox"].append(
        {
            "id": "reply-1",
            "from": "Office <office@civil.iitd.ac.in>",
            "to": ["student@example.edu"],
            "subject": "Re: Project form",
            "date": "2026-09-25T09:00:00+00:00",
            "body": "Signed.",
        }
    )
    bridge.state.write(state)
    again = bridge.list_followups(days=3, today=date(2026, 10, 3))
    assert again["waiting"] == []


def test_attachment_text_and_send(bridge, tmp_path):
    state = bridge.state.read()
    state["extra_inbox"] = [
        {
            "id": "notice-1",
            "from": "office@civil.iitd.ac.in",
            "to": ["student@example.edu"],
            "subject": "Notice",
            "date": "2026-10-02T09:00:00+00:00",
            "body": "See the notice.",
            "attachments": [
                {"filename": "notice.txt", "content_type": "text/plain", "text": "Meet at LH 111", "size": 15}
            ],
        }
    ]
    bridge.state.write(state)
    read = bridge.read_attachment("notice-1", "notice.txt")
    assert "LH 111" in read["text"]

    note = tmp_path / "note.txt"
    note.write_text("bring the form", encoding="utf-8")
    bridge.enable_send()
    draft = bridge.compose_mail("ada@example.edu", "Form", "Attached.", attachments=[str(note)])
    sent = bridge.request_send(draft["id"])
    assert sent["status"] == "sent"
    assert sent["sent_proof"] == "found"
    assert sent["sent_folder"] == "Sent"
    assert sent["attachments"] == ["note.txt"]


def test_pdf_text_extracts():
    data = _tiny_pdf("Meet at LH 111")
    text = attachment_text("notice.pdf", "application/pdf", data)
    assert "LH 111" in text


def test_calendar_event_from_lecture(bridge):
    state = bridge.state.read()
    state["extra_inbox"] = [
        {
            "id": "lecture-1",
            "from": "office@civil.iitd.ac.in",
            "to": ["student@example.edu"],
            "subject": "Lecture 7",
            "date": "2026-10-01T09:00:00+00:00",
            "body": LECTURE,
        }
    ]
    bridge.state.write(state)
    event = bridge.extract_calendar_event("lecture-1", today=date(2026, 10, 3))
    assert event["start"].startswith("2026-10-07T17:00:00")
    assert event["location"] == "LH 111"
    assert event["url"] == "https://example.edu/lecture7"
    added = bridge.add_calendar_event(message_id="lecture-1", apply_local=False, today=date(2026, 10, 3))
    assert Path(added["ics"]).read_text(encoding="utf-8").startswith("BEGIN:VCALENDAR")
    assert "calendar.google.com" in added["google_url"]
    assert added["calendar"]["added"] is False


def test_reply_is_in_the_action_log(bridge):
    draft = bridge.save_draft("demo-course", "I will submit the assignment before the deadline on the portal.")
    rows = bridge.list_actions(kind="reply")
    assert rows[-1]["draft_id"] == draft["id"]
    assert rows[-1]["message_id"] == "demo-course"
    assert "assignment" in rows[-1]["body"]
    opened = bridge.get_action(rows[-1]["id"])
    assert opened["status"] == "drafted"


def test_secret_attachment_and_header_and_directory_are_refused(bridge, tmp_path):
    secret = tmp_path / ".ssh"
    secret.mkdir()
    key = secret / "id_rsa"
    key.write_text("secret", encoding="utf-8")
    with pytest.raises(BridgeError, match="credential"):
        bridge.compose_mail("ada@example.edu", "Keys", "no", attachments=[str(key)])
        bridge.enable_send()
        draft = bridge.compose_mail("ada@example.edu", "Keys", "no", attachments=[str(key)])
        bridge.request_send(draft["id"])

    bridge.enable_send()
    with pytest.raises(BridgeError, match="line break"):
        bridge.compose_mail("ada@example.edu\r\nBcc: eve@evil.test", "Hello", "body")
    with pytest.raises(BridgeError, match="line break"):
        bridge.compose_mail("ada@example.edu", "Hello\r\nBcc: eve@evil.test", "body")

    from assistant_bridge.directory import curl_fetch_args, faculty_host, fetch_public_page

    assert faculty_host("https://civil.iitd.ac.in/faculty")
    assert not faculty_host("https://evil.example/faculty")
    assert "--max-redirs" in curl_fetch_args("https://civil.iitd.ac.in/faculty")
    with pytest.raises(BridgeError, match="outside IIT Delhi"):
        fetch_public_page("https://evil.example/steal")

    from assistant_bridge.audit import redact_log
    from assistant_bridge.events import _apple

    assert "ab_supersecret" not in redact_log("GET /mcp?token=ab_supersecret HTTP/1.1")
    script = _apple('Hello"\ndo shell script "id"')
    assert "\n" not in script
    assert script.startswith('"') and script.endswith('"')


def test_sent_folder_preference():
    assert choose_sent_folders(["INBOX", "INBOX/Sent", "Drafts"]) == ["INBOX/Sent"]


def _tiny_pdf(text: str) -> bytes:
    content = f"BT /F1 18 Tf 36 100 Td ({text}) Tj ET"
    objects = []
    objects.append("1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")
    objects.append("2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n")
    objects.append(
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    )
    objects.append(f"4 0 obj<</Length {len(content)}>>stream\n{content}\nendstream\nendobj\n")
    objects.append("5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n")
    body = b"".join(part.encode("ascii") for part in objects)
    header = b"%PDF-1.4\n"
    offsets = [0]
    cursor = len(header)
    for part in objects:
        offsets.append(cursor)
        cursor += len(part.encode("ascii"))
    xref_at = cursor + len(header) - len(header) + len(header)
    # rebuild with known offsets
    chunks = [header]
    positions = [0]
    for part in objects:
        positions.append(sum(len(chunk) for chunk in chunks))
        chunks.append(part.encode("ascii"))
    startxref = sum(len(chunk) for chunk in chunks)
    xref = ["xref\n0 6\n", "0000000000 65535 f \n"]
    for pos in positions[1:]:
        xref.append(f"{pos:010d} 00000 n \n")
    trailer = f"trailer<</Size 6/Root 1 0 R>>\nstartxref\n{startxref}\n%%EOF\n"
    return b"".join(chunks) + "".join(xref).encode("ascii") + trailer.encode("ascii")
