from __future__ import annotations

import os
import urllib.parse

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from assistant_bridge import __version__
from assistant_bridge.accounts import SESSION_COOKIE, bind_mailbox, open_session, seal_session
from assistant_bridge.errors import BridgeError
from assistant_bridge.mcp_server import handle_message
from assistant_bridge.oidc import (
    authorize_url,
    connected_page,
    error_page,
    finish_login,
    load_client,
    mail_form,
    new_login,
    redirect_for,
    docs_page,
    guide_page,
    privacy_page,
    self_host_page,
    sign_in_page,
    take_login,
)
from assistant_bridge.service import Bridge
from assistant_bridge.tokens import require_scopes

_READ = ("read",)
_DRAFT = ("draft",)
_SEND = ("send",)
_READ_OR_DRAFT = ("read", "draft")


class SummarizeIn(BaseModel):
    message_id: str | None = None
    subject: str | None = None
    text: str | None = None


class MessageIn(BaseModel):
    message_id: str


class ComposeIn(BaseModel):
    to: str
    subject: str
    body: str


class DraftIn(BaseModel):
    message_id: str
    body: str
    subject: str | None = None


class CheckIn(BaseModel):
    text: str
    message_id: str | None = None


class SendRequestIn(BaseModel):
    draft_id: str


class SendNowIn(BaseModel):
    draft_id: str
    phrase: str = Field(default="", description="Ignored. Sending does not need a phrase.")


class ConfirmIn(BaseModel):
    id: str
    confirm_code: str = Field(description="Code printed by webmail-mcp approve. Do not invent one.")


class SearchIn(BaseModel):
    text: str | None = None
    sender: str | None = None
    subject: str | None = None
    limit: int = 20
    folder: str = "INBOX"


class LatestIn(BaseModel):
    limit: int = 10
    folder: str = "INBOX"


def create_app(bridge: Bridge | None = None) -> FastAPI:
    app = FastAPI(title="webmail-mcp", version=__version__)
    app.state.bridge = bridge
    app.state.oidc_pending = {}

    @app.exception_handler(BridgeError)
    def _bridge_error(_request: Request, exc: BridgeError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.get("/health")
    def health():
        return {"ok": True, "service": "webmail-mcp", "version": __version__}

    @app.get("/", response_class=HTMLResponse)
    def sign_in():
        return HTMLResponse(sign_in_page())

    @app.get("/setup", response_class=HTMLResponse)
    def setup():
        return HTMLResponse(docs_page())

    @app.get("/guide", response_class=HTMLResponse)
    def guide():
        return HTMLResponse(guide_page())

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return HTMLResponse(privacy_page())

    @app.get("/self-host", response_class=HTMLResponse)
    def self_host():
        return HTMLResponse(self_host_page())

    @app.get("/auth/login")
    def auth_login(request: Request):
        bridge = _open_bridge(request)
        client = load_client(bridge.home)
        redirect_uri = redirect_for(request.url.hostname, client["redirect_uris"])
        login = new_login()
        request.app.state.oidc_pending[login.state] = login
        return RedirectResponse(authorize_url(client, redirect_uri, login), status_code=302)

    @app.get("/auth/callback", response_class=HTMLResponse)
    def auth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        error_description: str = "",
    ):
        if error:
            return HTMLResponse(error_page(error_description or error), status_code=400)
        bridge = _open_bridge(request)
        try:
            client = load_client(bridge.home)
            redirect_uri = redirect_for(request.url.hostname, client["redirect_uris"])
            login = take_login(request.app.state.oidc_pending, state)
            if not code:
                raise BridgeError("sign-in did not return a code", status=400)
            profile = finish_login(client, redirect_uri, code, login.verifier)
            sealed = seal_session(bridge.home, profile)
        except BridgeError as exc:
            return HTMLResponse(error_page(exc.message), status_code=exc.status)
        response = RedirectResponse("/auth/mail", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            sealed,
            httponly=True,
            samesite="lax",
            max_age=3600,
            path="/",
        )
        return response

    @app.get("/auth/mail", response_class=HTMLResponse)
    def auth_mail_form(request: Request):
        bridge = _open_bridge(request)
        try:
            session = open_session(bridge.home, request.cookies.get(SESSION_COOKIE))
        except BridgeError as exc:
            return HTMLResponse(error_page(exc.message), status_code=exc.status)
        if bridge.accounts.has_mail(session["kerberos"]):
            return HTMLResponse(connected_page(session, None))
        return HTMLResponse(mail_form(session))

    @app.post("/auth/mail", response_class=HTMLResponse)
    async def auth_mail_connect(request: Request):
        bridge = _open_bridge(request)
        try:
            session = open_session(bridge.home, request.cookies.get(SESSION_COOKIE))
            fields = urllib.parse.parse_qs((await request.body()).decode(errors="replace"))
            password = (fields.get("password") or [""])[0]
            raw = bridge.accounts.connect(session, password)
        except BridgeError as exc:
            session = _session_or_none(bridge, request)
            if session is None:
                return HTMLResponse(error_page(exc.message), status_code=exc.status)
            return HTMLResponse(mail_form(session, exc.message), status_code=exc.status)
        url = None
        if raw:
            url = _connector_url(request, raw)
        bridge.audit.record("mail_connected", ok=True, detail={"kerberos": session["kerberos"]})
        return HTMLResponse(connected_page(session, url))

    @app.post("/mcp")
    async def mcp(
        request: Request,
        authorization: str | None = Header(default=None),
        token: str | None = None,
    ):
        # ChatGPT's "No Auth" connector cannot send a header, so the same token may arrive as ?token=.
        raw = _presented_token(authorization, token)
        bridge, _record = _auth(request, f"Bearer {raw}" if raw else None, ("read", "draft", "send"), mode="any")
        try:
            payload = await request.json()
        except Exception as exc:
            raise BridgeError("MCP body must be a JSON-RPC object") from exc
        if not isinstance(payload, dict):
            raise BridgeError("MCP body must be a JSON-RPC object")
        response = handle_message(bridge, payload, token=raw)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)

    @app.get("/mcp")
    def mcp_get():
        return JSONResponse(
            {"detail": "POST JSON-RPC to /mcp. This is a stateless streamable HTTP MCP endpoint."},
            status_code=405,
            headers={"Allow": "POST"},
        )

    @app.get("/v1/inbox")
    def inbox(
        request: Request,
        authorization: str | None = Header(default=None),
        limit: int = 20,
        folder: str = "INBOX",
    ):
        bridge, token = _auth(request, authorization, _READ)
        return {"messages": bridge.list_inbox(limit=limit, folder=folder, token_id=token["id"])}

    @app.get("/v1/inbox/{message_id}")
    def message(message_id: str, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return bridge.get_message(message_id, token_id=token["id"])

    @app.post("/v1/summarize")
    def summarize(body: SummarizeIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return bridge.summarize_input(
            message_id=body.message_id,
            subject=body.subject,
            text=body.text,
            token_id=token["id"],
        )

    @app.post("/v1/reply-outline")
    def outline(body: MessageIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ_OR_DRAFT, mode="any")
        return bridge.outline_reply(body.message_id, token_id=token["id"])

    @app.post("/v1/compose")
    def compose(body: ComposeIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _DRAFT)
        return bridge.compose_mail(body.to, body.subject, body.body, token_id=token["id"])

    @app.get("/v1/drafts")
    def drafts(request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _DRAFT)
        return {"drafts": bridge.list_drafts(token_id=token["id"])}

    @app.post("/v1/drafts")
    def save_draft(body: DraftIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _DRAFT)
        return bridge.save_draft(body.message_id, body.body, body.subject, token_id=token["id"])

    @app.post("/v1/check")
    def check(body: CheckIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ_OR_DRAFT, mode="any")
        return bridge.check_text(body.text, body.message_id, token_id=token["id"])

    @app.post("/v1/sync")
    def sync(request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return bridge.sync_inbox(token_id=token["id"])

    @app.get("/v1/briefing")
    def briefing(request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return bridge.get_briefing(token_id=token["id"])

    @app.post("/v1/search")
    def search(body: SearchIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return {
            "messages": bridge.search_inbox(
                text=body.text,
                sender=body.sender,
                subject=body.subject,
                limit=body.limit,
                folder=body.folder,
                token_id=token["id"],
            )
        }

    @app.post("/v1/latest")
    def latest(body: LatestIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return bridge.latest_brief(limit=body.limit, folder=body.folder, token_id=token["id"])

    @app.get("/v1/deadlines")
    def deadlines(
        request: Request,
        authorization: str | None = Header(default=None),
        limit: int = 20,
        folder: str = "INBOX",
    ):
        bridge, token = _auth(request, authorization, _READ)
        return bridge.find_deadlines(limit=limit, folder=folder, token_id=token["id"])

    @app.get("/v1/folders")
    def folders(request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return {"folders": bridge.list_folders(token_id=token["id"])}

    @app.get("/v1/probe")
    def probe(request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _READ)
        return bridge.probe_mailbox(token_id=token["id"])

    @app.get("/v1/actions")
    def actions(
        request: Request,
        authorization: str | None = Header(default=None),
        kind: str = "",
        limit: int = 50,
    ):
        bridge, token = _auth(request, authorization, _READ)
        return {"actions": bridge.list_actions(kind=kind or None, limit=limit, token_id=token["id"])}

    @app.get("/v1/audit")
    def audit(request: Request, authorization: str | None = Header(default=None), limit: int = 50):
        bridge, token = _auth(request, authorization, _READ)
        return {"events": bridge.list_audit(limit, token_id=token["id"])}

    @app.post("/v1/send/request")
    def send_request(body: SendRequestIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _SEND)
        return bridge.request_send(body.draft_id, token_id=token["id"])

    @app.post("/v1/send/now")
    def send_now(body: SendNowIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _SEND)
        return bridge.send_now(body.draft_id, body.phrase, token_id=token["id"])

    @app.post("/v1/send/confirm")
    def confirm(body: ConfirmIn, request: Request, authorization: str | None = Header(default=None)):
        bridge, token = _auth(request, authorization, _SEND)
        return bridge.confirm_send(body.id, body.confirm_code, token_id=token["id"])

    return app


def _connector_url(request: Request, raw: str) -> str:
    public = os.environ.get("ASSISTANT_BRIDGE_PUBLIC_URL", "").strip().rstrip("/")
    base = public or str(request.base_url).rstrip("/")
    return base + "/mcp?token=" + urllib.parse.quote(raw)


def _open_bridge(request: Request) -> Bridge:
    bridge: Bridge | None = request.app.state.bridge
    if bridge is None:
        bridge = Bridge.open()
        request.app.state.bridge = bridge
    return bridge


def _presented_token(authorization: str | None, query_token: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return (query_token or "").strip()


def _auth(request: Request, authorization: str | None, scopes: tuple[str, ...], mode: str = "all"):
    bridge: Bridge | None = request.app.state.bridge
    if bridge is None:
        bridge = Bridge.open()
        request.app.state.bridge = bridge
    if not authorization or not authorization.lower().startswith("bearer "):
        raise BridgeError("missing bearer token", status=401)
    raw = authorization.split(" ", 1)[1].strip()
    record = bridge.tokens.verify(raw)
    if record is None:
        raise BridgeError("invalid token", status=401)
    require_scopes(record, scopes, mode=mode)
    bind_mailbox(bridge.accounts.kerberos_for_token(record["id"]))
    return bridge, record


def _session_or_none(bridge: Bridge, request: Request) -> dict | None:
    try:
        return open_session(bridge.home, request.cookies.get(SESSION_COOKIE))
    except BridgeError:
        return None
