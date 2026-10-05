import json

import pytest

from assistant_bridge.errors import BridgeError

REPLY = (
    "Hello Professor Sharma, I will submit the assignment on the course portal "
    "before the deadline, and I confirm that I will take the optional viva."
)


def test_demo_list_summarize_draft_and_checked_send(bridge, home):
    inbox = bridge.list_inbox()
    assert len(inbox) == 4
    subjects = {item["subject"].lower() for item in inbox}
    assert any("deadline" in subject for subject in subjects)
    assert any("internship" in subject for subject in subjects)
    assert any("poster" in subject for subject in subjects)
    assert any("newsletter" in subject for subject in subjects)

    summary = bridge.summarize_message("demo-course")
    assert summary["headline"]
    assert summary["bullets"]
    assert summary["asks"]
    assert summary["deadlines"]
    assert "video" not in " ".join(summary["deadlines"]).lower()
    assert summary["suggested_action"]

    draft = bridge.save_draft("demo-course", REPLY)
    assert draft["kind"] == "reply"
    assert draft["message_id"] == "demo-course"
    checked = bridge.check_text(draft["body"], "demo-course")
    assert checked["ok"] is True
    assert checked["warnings"] == []

    with pytest.raises(BridgeError, match="send is disabled"):
        bridge.request_send(draft["id"])

    bridge.enable_send()
    sent = bridge.request_send(draft["id"])
    assert sent["status"] == "sent"
    assert sent["via"] == "mock"
    assert "approve" not in json.dumps(sent)

    state = (home / "state.json").read_text(encoding="utf-8")
    assert "sent" in state
