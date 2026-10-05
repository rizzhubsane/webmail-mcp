import io
import json

from assistant_bridge.mcp_server import PROTOCOL, handle_message, read_message, write_message


def test_search_sent_uses_the_sent_folder(bridge, monkeypatch):
    class Box:
        def list_folders(self):
            return ["INBOX", "Sent"]

        def search(self, **kwargs):
            assert kwargs["folder"] == "Sent"
            assert kwargs["text"] == "reminor"
            return [
                {
                    "id": "sent-1",
                    "from": "ce1240041@iitd.ac.in",
                    "to": ["rohith@iitd.ac.in"],
                    "subject": "Reminor request",
                    "date": "2026-04-01T00:00:00+00:00",
                    "body": "Please allow me to appear for the reminor exam.",
                }
            ]

        def list_inbox(self, **kwargs):
            return []

    monkeypatch.setattr(bridge, "connector", lambda: Box())
    rows = bridge.search_sent(text="reminor")
    assert rows[0]["subject"] == "Reminor request"
    assert rows[0]["folder"] == "Sent"


def test_initialize_tools_and_summarize(bridge):
    raw, _ = bridge.tokens.create("mcp", ["read"])
    init = handle_message(
        bridge,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
        },
    )
    instructions = init["result"]["instructions"]
    assert init["result"]["protocolVersion"] == PROTOCOL
    assert "Credentials stay on this server." in instructions
    assert "Never ask for a webmail password in chat." in instructions
    assert "do not invent a confirm code" in instructions

    assert handle_message(bridge, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handle_message(bridge, {"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}

    tools = handle_message(bridge, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    names = [tool["name"] for tool in tools["result"]["tools"]]
    assert "summarize_message" in names
    assert "request_send" in names
    assert "send_now" in names
    assert "search_sent" in names
    assert "call search_sent" in instructions
    assert init["result"]["serverInfo"]["name"] == "webmail-mcp"

    called = handle_message(
        bridge,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "summarize_message", "arguments": {"message_id": "demo-course"}},
        },
        token=raw,
    )
    assert called["result"]["isError"] is False
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["id"] == "demo-course"
    assert payload["headline"]
    assert payload["bullets"]
    assert "video" not in " ".join(payload["deadlines"]).lower()


def test_stdio_content_length_framing():
    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    buffer = io.BytesIO()
    write_message(buffer, payload)
    buffer.seek(0)
    assert read_message(buffer) == payload
