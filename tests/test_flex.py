from fastapi.testclient import TestClient

from assistant_bridge.connectors.imap_conn import imap_quoted
from assistant_bridge.http_api import create_app
from assistant_bridge.mcp_server import handle_message


def test_search_brief_deadlines_and_folders(bridge):
    found = bridge.search_inbox(subject="internship")
    assert [item["id"] for item in found] == ["demo-intern"]

    brief = bridge.latest_brief(limit=2)
    assert len(brief["messages"]) == 2
    assert brief["messages"][0]["summary"]["headline"]
    assert "preview" in brief["messages"][0]

    deadlines = bridge.find_deadlines(limit=10)
    ids = {item["id"] for item in deadlines["items"]}
    assert "demo-course" in ids
    assert "demo-news" not in ids
    assert all("video" not in " ".join(item["deadlines"]).lower() for item in deadlines["items"])

    assert bridge.list_folders() == ["INBOX"]


def test_search_quoting_strips_imap_breaks():
    assert imap_quoted('cafe "OR ALL') == '"cafe OR ALL"'


def test_http_mcp_for_any_client(bridge):
    raw, _record = bridge.tokens.create("claude", ["read"])
    with TestClient(create_app(bridge)) as client:
        denied = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert denied.status_code == 401
        assert client.get("/mcp").status_code == 405
        query_auth = client.post(
            f"/mcp?token={raw}",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert query_auth.status_code == 200
        assert query_auth.json()["result"] == {}

        headers = {"Authorization": f"Bearer {raw}"}
        init = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "claude", "version": "1"}},
            },
        )
        assert init.status_code == 200
        assert init.json()["result"]["protocolVersion"] == "2025-03-26"
        assert "Never ask for a webmail password" in init.json()["result"]["instructions"]

        called = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "latest_brief", "arguments": {"limit": 2}},
            },
        )
        assert called.status_code == 200
        assert called.json()["result"]["isError"] is False

        names = [
            tool["name"]
            for tool in handle_message(bridge, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
        ]
        assert {"search_inbox", "latest_brief", "find_deadlines", "list_folders"} <= set(names)
