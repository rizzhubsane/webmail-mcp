import pytest

from assistant_bridge.errors import BridgeError

BODY = (
    "Hello, I am writing to confirm that I can meet during the afternoon slot "
    "and I will bring the signed form with me."
)


def test_compose_and_command_phrase_send_now(bridge, home):
    draft = bridge.compose_mail("ada@example.edu", "Afternoon slot", BODY)
    assert draft["kind"] == "new"
    assert draft["message_id"] is None
    assert draft["to"] == ["ada@example.edu"]

    with pytest.raises(BridgeError, match="send is disabled"):
        bridge.send_now(draft["id"])

    bridge.enable_send()
    sent = bridge.send_now(draft["id"])
    assert sent["status"] == "sent"
    assert sent["via"] == "mock"
    assert len(bridge.state.read()["sent"]) == 1
