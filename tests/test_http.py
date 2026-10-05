import pytest
from fastapi.testclient import TestClient

from assistant_bridge.http_api import create_app
from assistant_bridge.service import assert_local_bind


@pytest.fixture
def client(bridge):
    with TestClient(create_app(bridge)) as test_client:
        yield test_client


def _bearer(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_health_and_missing_token(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    denied = client.get("/v1/inbox")
    assert denied.status_code == 401


def test_read_token_cannot_create_drafts(client, bridge):
    read_token, _ = bridge.tokens.create("read", ["read"])
    draft_token, _ = bridge.tokens.create("draft", ["draft"])
    body = {
        "to": "ada@example.edu",
        "subject": "Hello",
        "body": "Hello, this is a new note with enough detail to stand alone.",
    }

    denied = client.post("/v1/compose", json=body, headers=_bearer(read_token))
    assert denied.status_code == 403

    created = client.post("/v1/compose", json=body, headers=_bearer(draft_token))
    assert created.status_code == 200
    payload = created.json()
    assert payload["kind"] == "new"
    assert payload["message_id"] is None

    reply = {
        "message_id": "demo-course",
        "body": "Hello Professor Sharma, I confirm that I will take the optional viva.",
    }
    blocked = client.post("/v1/drafts", json=reply, headers=_bearer(read_token))
    assert blocked.status_code == 403
    saved = client.post("/v1/drafts", json=reply, headers=_bearer(draft_token))
    assert saved.status_code == 200
    assert saved.json()["kind"] == "reply"

    inbox = client.get("/v1/inbox", headers=_bearer(read_token))
    assert inbox.status_code == 200
    assert len(inbox.json()["messages"]) == 4


def test_refuses_public_bind(monkeypatch):
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    with pytest.raises(Exception, match="0.0.0.0"):
        assert_local_bind("0.0.0.0")
    assert assert_local_bind("localhost") == "127.0.0.1"
    monkeypatch.setenv("WEBSITE_SITE_NAME", "webmail-mcp")
    assert assert_local_bind("0.0.0.0") == "0.0.0.0"
