from __future__ import annotations

import json
import os
import sys

from assistant_bridge import __version__
from assistant_bridge.errors import BridgeError
from assistant_bridge.service import Bridge
from assistant_bridge.tokens import require_scopes

INSTRUCTIONS = (
    "Credentials stay on this server. "
    "Never ask for a webmail password in chat. "
    "When the user names a person, call resolve_person before composing. "
    "When they mention mail they sent, an earlier mail of theirs, or a reminder of what they wrote, call search_sent. "
    "That mail is not in INBOX. Do not report a mailbox error after looking only at received mail. "
    "When they mean a conversation, call get_thread. "
    "Call list_followups for mail waiting on a reply and for deadlines due tomorrow. "
    "Call read_attachment for a PDF or notice. "
    "Call extract_event, then add_calendar_event only when they ask to add it to the calendar. "
    "When the user says to send, call request_send or send_now. That transmits the draft. "
    "Do not ask them to run a local approve command, and do not invent a confirm code or a command phrase. "
    "Replies and sends are stored in the action log; use list_actions to revisit them."
)

PROTOCOL = "2024-11-05"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

_TOOLS = [
    {
        "name": "list_inbox",
        "description": "List mailbox messages. Credentials stay on this server.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Newest messages to return, 1 to 40."},
                "folder": {"type": "string", "description": "Mailbox folder. Defaults to INBOX."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_inbox",
        "description": "Search received mail in INBOX. For mail the user sent, call search_sent instead.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "sender": {"type": "string", "description": "From address or name."},
                "subject": {"type": "string"},
                "limit": {"type": "integer"},
                "folder": {"type": "string", "description": "Received-mail folder. Defaults to INBOX. Do not use this for sent mail."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_sent",
        "description": "Find mail the user sent. Use this when they say they sent a mail, ask for their earlier email, or want a draft based on something they already wrote.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Words from the sent mail, such as reminor or the professor's name."},
                "sender": {"type": "string"},
                "subject": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "latest_brief",
        "description": "Newest messages with a short preview and an offline summary. Prefer this when the user asks for the latest mail.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "folder": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "find_deadlines",
        "description": "Scan recent mail and return only messages that contain a real deadline. Does not treat 'by video' as a deadline.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "folder": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_folders",
        "description": "List mailbox folders. Does not change or delete anything.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_message",
        "description": "Read one message by id.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "summarize_message",
        "description": "Offline extractive summary: headline, bullets, asks, deadlines, suggested action.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "outline_reply",
        "description": "Build a reply skeleton with [placeholders]. Does not send.",
        "scopes": ("read", "draft"),
        "mode": "any",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compose_mail",
        "description": "Create a new draft (kind=new) with to, subject, and body. No message_id. Does not send.",
        "scopes": ("draft",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_drafts",
        "description": "List drafts saved on this machine.",
        "scopes": ("draft",),
        "mode": "all",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "save_draft",
        "description": "Save a reply draft for an existing message. Does not send.",
        "scopes": ("draft",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "body": {"type": "string"},
                "subject": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["message_id", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_text",
        "description": "Blocking checks: too short, leftover [placeholders]. Warnings: slang, hard promises, emails, phones, unanswered asks.",
        "scopes": ("read", "draft"),
        "mode": "any",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "message_id": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_inbox",
        "description": "Record seen mail. The first sync reports 0 new. Later syncs report only new mail and summaries.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_briefing",
        "description": "Return the briefing saved by the latest sync.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "probe_mailbox",
        "description": "Test the connector. Never returns a password or token.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "request_send",
        "description": "Send this draft now. Call it when the user says to send. Do not ask them to approve it locally.",
        "scopes": ("send",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"draft_id": {"type": "string"}},
            "required": ["draft_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_now",
        "description": "Send this draft now. A phrase is not required.",
        "scopes": ("send",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"draft_id": {"type": "string"}, "phrase": {"type": "string"}},
            "required": ["draft_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "resolve_person",
        "description": "Resolve a name and department to an email from the local directory and people already seen in mail. Ask which match if none is chosen.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "refresh_directory",
        "description": "Refresh the local name book from public IIT Delhi faculty pages. Does not use the mailbox password.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "learn_contacts",
        "description": "Remember names and emails from recent mail headers.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_thread",
        "description": "Group one conversation, including sent replies, so a subject is not guessed among copies.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_followups",
        "description": "Mail you sent with no reply after a few days, and deadlines due tomorrow.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_attachment",
        "description": "Read text from a PDF or text attachment on a message.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}, "filename": {"type": "string"}},
            "required": ["message_id", "filename"],
            "additionalProperties": False,
        },
    },
    {
        "name": "extract_event",
        "description": "Pull a date, time, place, and link from a message. Does not add it to the calendar.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_calendar_event",
        "description": "Add the event to Calendar when the user asks. Also writes an .ics file and a Google Calendar link.",
        "scopes": ("draft",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "title": {"type": "string"},
                "start": {"type": "string"},
                "location": {"type": "string"},
                "url": {"type": "string"},
                "notes": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_actions",
        "description": "Revisit actions taken through this bridge, especially replies and sends.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"kind": {"type": "string"}, "limit": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_action",
        "description": "Open one logged action, including the reply text.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"action_id": {"type": "string"}},
            "required": ["action_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_audit",
        "description": "Recent local audit events. Actions only; no passwords or raw tokens.",
        "scopes": ("read",),
        "mode": "all",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
]


def handle_message(bridge: Bridge, message: dict, token: str | None = None) -> dict | None:
    if token is None:
        token = os.environ.get("ASSISTANT_BRIDGE_TOKEN")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not message.get("method"):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "invalid request")
    method = message["method"]
    if method.startswith("notifications/"):
        return None
    request_id = message.get("id")
    params = message.get("params") or {}
    try:
        if method == "initialize":
            requested = str((params or {}).get("protocolVersion") or PROTOCOL)
            result = {
                "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "webmail-mcp", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": tool["name"], "description": tool["description"], "inputSchema": tool["inputSchema"]}
                    for tool in _TOOLS
                ]
            }
        elif method == "tools/call":
            result = _call_tool(bridge, params, token)
        else:
            return _error(request_id, -32601, "method not found")
    except BridgeError as exc:
        return _result(request_id, _tool_error(exc.message))
    except Exception as exc:  # noqa: BLE001 — MCP must return a JSON-RPC error, not crash the stdio loop
        return _error(request_id, -32603, type(exc).__name__)
    return _result(request_id, result)


def _call_tool(bridge: Bridge, params: dict, token: str | None) -> dict:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    tool = next((item for item in _TOOLS if item["name"] == name), None)
    if tool is None:
        raise BridgeError("unknown tool")
    record = bridge.tokens.verify(token)
    if record is None:
        raise BridgeError("invalid token", status=401)
    require_scopes(record, tool["scopes"], mode=tool["mode"])
    token_id = record["id"]
    try:
        payload = _dispatch(bridge, name, arguments, token_id)
    except BridgeError:
        bridge.note_tool(name, arguments, None, ok=False, token_id=token_id)
        raise
    bridge.note_tool(name, arguments, payload, ok=True, token_id=token_id)
    return _tool_ok(payload)


def _dispatch(bridge: Bridge, name: str, arguments: dict, token_id: str):
    if name == "list_inbox":
        payload = {
            "messages": bridge.list_inbox(
                limit=arguments.get("limit") or 20,
                folder=arguments.get("folder") or "INBOX",
                token_id=token_id,
            )
        }
    elif name == "search_inbox":
        payload = {
            "messages": bridge.search_inbox(
                text=arguments.get("text"),
                sender=arguments.get("sender"),
                subject=arguments.get("subject"),
                limit=arguments.get("limit") or 20,
                folder=arguments.get("folder") or "INBOX",
                token_id=token_id,
            )
        }
    elif name == "search_sent":
        payload = {
            "messages": bridge.search_sent(
                text=arguments.get("text"),
                sender=arguments.get("sender"),
                subject=arguments.get("subject"),
                limit=arguments.get("limit") or 20,
                token_id=token_id,
            )
        }
    elif name == "latest_brief":
        payload = bridge.latest_brief(
            limit=arguments.get("limit") or 10,
            folder=arguments.get("folder") or "INBOX",
            token_id=token_id,
        )
    elif name == "find_deadlines":
        payload = bridge.find_deadlines(
            limit=arguments.get("limit") or 20,
            folder=arguments.get("folder") or "INBOX",
            token_id=token_id,
        )
    elif name == "list_folders":
        payload = {"folders": bridge.list_folders(token_id=token_id)}
    elif name == "get_message":
        payload = bridge.get_message(arguments["message_id"], token_id=token_id)
    elif name == "summarize_message":
        payload = bridge.summarize_message(arguments["message_id"], token_id=token_id)
    elif name == "outline_reply":
        payload = bridge.outline_reply(arguments["message_id"], token_id=token_id)
    elif name == "compose_mail":
        payload = bridge.compose_mail(
            arguments["to"],
            arguments["subject"],
            arguments["body"],
            arguments.get("attachments"),
            token_id=token_id,
        )
    elif name == "list_drafts":
        payload = {"drafts": bridge.list_drafts(token_id=token_id)}
    elif name == "save_draft":
        payload = bridge.save_draft(
            arguments["message_id"],
            arguments["body"],
            arguments.get("subject"),
            arguments.get("attachments"),
            token_id=token_id,
        )
    elif name == "check_text":
        payload = bridge.check_text(arguments["text"], arguments.get("message_id"), token_id=token_id)
    elif name == "sync_inbox":
        payload = bridge.sync_inbox(token_id=token_id)
    elif name == "get_briefing":
        payload = bridge.get_briefing(token_id=token_id)
    elif name == "probe_mailbox":
        payload = bridge.probe_mailbox(token_id=token_id)
    elif name == "request_send":
        payload = bridge.request_send(arguments["draft_id"], token_id=token_id)
    elif name == "send_now":
        payload = bridge.send_now(arguments["draft_id"], arguments.get("phrase") or "", token_id=token_id)
    elif name == "resolve_person":
        payload = bridge.resolve_person(arguments["query"], token_id=token_id)
    elif name == "refresh_directory":
        payload = bridge.refresh_directory(token_id=token_id)
    elif name == "learn_contacts":
        payload = bridge.learn_contacts(limit=int(arguments.get("limit") or 40), token_id=token_id)
    elif name == "get_thread":
        payload = bridge.get_thread(arguments["message_id"], token_id=token_id)
    elif name == "list_followups":
        payload = bridge.list_followups(days=int(arguments.get("days") or 3), token_id=token_id)
    elif name == "read_attachment":
        payload = bridge.read_attachment(arguments["message_id"], arguments["filename"], token_id=token_id)
    elif name == "extract_event":
        payload = bridge.extract_calendar_event(arguments["message_id"], token_id=token_id)
    elif name == "add_calendar_event":
        payload = bridge.add_calendar_event(
            message_id=arguments.get("message_id"),
            title=arguments.get("title"),
            start=arguments.get("start"),
            location=arguments.get("location"),
            url=arguments.get("url"),
            notes=arguments.get("notes"),
            token_id=token_id,
        )
    elif name == "list_actions":
        payload = {"actions": bridge.list_actions(kind=arguments.get("kind") or None, limit=int(arguments.get("limit") or 50), token_id=token_id)}
    elif name == "get_action":
        payload = bridge.get_action(arguments["action_id"], token_id=token_id)
    elif name == "list_audit":
        payload = {"events": bridge.list_audit(int(arguments.get("limit") or 50), token_id=token_id)}
    else:
        raise BridgeError("unknown tool")
    return payload


def serve_stdio(bridge: Bridge) -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        message = read_message(stdin)
        if message is None:
            return
        response = handle_message(bridge, message)
        if response is not None:
            write_message(stdout, response)


def read_message(stream) -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8", errors="replace")
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stream, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stream.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    stream.flush()


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_ok(payload: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": False}


def _tool_error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}
