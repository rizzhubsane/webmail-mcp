import html
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from assistant_bridge.crypto import load_fernet
from assistant_bridge.http_api import create_app


@pytest.fixture
def client(bridge):
    with TestClient(create_app(bridge), base_url="http://127.0.0.1:8765") as test_client:
        yield test_client


def _write_client(home):
    secret = "test-client-secret"
    token = load_fernet(home).encrypt(secret.encode()).decode()
    (home / "oidc.json").write_text(
        json.dumps(
            {
                "client_id": "client-123",
                "client_secret": token,
                "redirect_uris": [
                    "http://127.0.0.1:8765/auth/callback",
                    "http://localhost:8765/auth/callback",
                ],
                "scopes": ["openid", "profile", "email", "iitd"],
            }
        ),
        encoding="utf-8",
    )
    return secret


def test_home_offers_iitd_sign_in(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Sign in with IIT Delhi" in page.text
    assert "Introducing IITD Webmail MCP" in page.text
    assert 'href="/auth/login"' in page.text
    assert 'href="/setup"' in page.text


def test_docs_explains_each_assistant(client):
    page = client.get("/setup")
    assert page.status_code == 200
    assert "Set up webmail-mcp" in page.text
    for name in ("ChatGPT", "Claude", "Gemini"):
        assert f'id="{name.lower()}"' in page.text
    assert 'id="cursor"' not in page.text
    assert "Copy Setup Prompt" in page.text
    assert 'class="prompt" hidden' in page.text
    assert "No Auth" in page.text
    assert "Streamable HTTP" in page.text
    assert 'href="/auth/login"' in page.text


def test_guide_privacy_and_self_host_pages(client):
    guide = client.get("/guide")
    privacy = client.get("/privacy")
    source = client.get("/self-host")
    assert guide.status_code == privacy.status_code == source.status_code == 200
    assert "search_sent" in guide.text
    assert "request_send" in guide.text
    assert "not an official IIT Delhi service" in privacy.text
    assert "https://github.com/rizzhubsane/webmail-mcp" in source.text
    home = client.get("/")
    assert 'href="/privacy"' in home.text
    assert 'href="/self-host"' in home.text


def test_login_redirects_to_devclub(client, home):
    secret = _write_client(home)
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://auth.devclub.in/api/oauth/authorize?")
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fauth%2Fcallback" in location
    assert "code_challenge_method=S256" in location
    assert secret not in location


def test_callback_shows_the_student(client, home, monkeypatch):
    _write_client(home)
    start = client.get("/auth/login", follow_redirects=False)
    query = urllib.parse.parse_qs(urllib.parse.urlparse(start.headers["location"]).query)
    state = query["state"][0]

    def fake_finish(client_data, redirect_uri, code, verifier):
        assert code == "abc"
        assert redirect_uri == "http://127.0.0.1:8765/auth/callback"
        assert client_data["client_secret"] == "test-client-secret"
        assert verifier
        return {
            "sub": "student-1",
            "name": "Sanidhya",
            "email": "cs1@iitd.ac.in",
            "kerberos": "cs1",
            "entry_number": "2024CS10001",
            "department": "CSE",
            "hostel": "Satpura",
        }

    monkeypatch.setattr("assistant_bridge.http_api.finish_login", fake_finish)
    done = client.get(f"/auth/callback?code=abc&state={state}", follow_redirects=False)
    assert done.status_code == 303
    assert done.headers["location"] == "/auth/mail"
    page = client.get("/auth/mail")
    assert page.status_code == 200
    assert "Sanidhya" in page.text
    assert "cs1" in page.text
    assert "Connect mail" in page.text


def test_callback_rejects_unknown_state(client, home):
    _write_client(home)
    done = client.get("/auth/callback?code=abc&state=missing")
    assert done.status_code == 400
    assert "start again" in done.text


def test_mail_password_is_stored_only_after_iitd_accepts(client, bridge, monkeypatch):
    from urllib.parse import unquote

    from assistant_bridge.accounts import bind_mailbox, seal_session
    from assistant_bridge.errors import BridgeError

    sealed = seal_session(bridge.home, {"sub": "student-1", "name": "Sanidhya", "kerberos": "cs1", "email": "cs1@iitd.ac.in"})
    client.cookies.set("wm_session", sealed)

    def reject(kerberos, password):
        raise BridgeError("mail password was not accepted", status=401)

    monkeypatch.setattr("assistant_bridge.accounts.accept_iitd_password", reject)
    rejected = client.post("/auth/mail", data={"password": "wrong-password"})
    assert rejected.status_code == 401
    assert "not accepted" in rejected.text
    assert not (bridge.home / "accounts.json").exists()

    monkeypatch.setattr("assistant_bridge.accounts.accept_iitd_password", lambda kerberos, password: None)
    saved = client.post("/auth/mail", data={"password": "mailbox-secret"})
    assert saved.status_code == 200
    assert "Paste this whole address into the assistant" in saved.text
    stored = (bridge.home / "accounts.json").read_text(encoding="utf-8")
    assert "mailbox-secret" not in stored
    assert bridge.accounts.has_mail("cs1")
    shown = html.unescape(saved.text.split('value="', 1)[1].split('"', 1)[0])
    token = unquote(shown.split("token=", 1)[1])
    record = bridge.tokens.verify(token)
    assert record is not None
    bind_mailbox(bridge.accounts.kerberos_for_token(record["id"]))
    seen = {}

    def fake_connector(kerberos):
        seen["kerberos"] = kerberos
        from assistant_bridge.connectors.mock import MockConnector

        return MockConnector(bridge.state.read)

    monkeypatch.setattr(bridge.accounts, "connector", fake_connector)
    try:
        bridge.list_inbox(token_id=record["id"])
        assert seen["kerberos"] == "cs1"
    finally:
        bind_mailbox(None)


def test_login_without_client_is_unavailable(bridge):
    with TestClient(create_app(bridge), base_url="http://127.0.0.1:8765") as client:
        missing = client.get("/auth/login", follow_redirects=False)
    assert missing.status_code == 503


def test_login_uses_azure_settings_when_file_missing(client, monkeypatch):
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-from-env")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "env-secret")
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert "client_id=client-from-env" in location
    assert "env-secret" not in location


def test_azure_host_uses_public_redirect(bridge, monkeypatch):
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-from-env")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("ASSISTANT_BRIDGE_PUBLIC_URL", "https://webmail-mcp.ariesiitd.com")
    with TestClient(create_app(bridge), base_url="https://webmail-mcp.azurewebsites.net") as client:
        response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert "redirect_uri=https%3A%2F%2Fwebmail-mcp.ariesiitd.com%2Fauth%2Fcallback" in location
    assert "env-secret" not in location
