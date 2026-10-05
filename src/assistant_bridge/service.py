from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from assistant_bridge.accounts import Accounts, current_mailbox
from assistant_bridge.audit import AuditLog
from assistant_bridge.checker import check_draft
from assistant_bridge.connectors import build_connector, iitd_settings, reject_webmail
from assistant_bridge.connectors.imap_conn import choose_sent_folders
from assistant_bridge.crypto import generate_master_key, write_master_key
from assistant_bridge.directory import (
    FACULTY_PAGES,
    fetch_public_page,
    ingest_html,
    lookup,
    merge_contacts,
    person_from_header,
)
from assistant_bridge.errors import BridgeError
from assistant_bridge.events import (
    IST,
    add_mac_event,
    attachment_text,
    extract_event,
    google_template,
    normalize_subject,
    parse_loose_date,
    write_ics,
)
from assistant_bridge.journal import Journal
from assistant_bridge.paths import resolve_home, secure_dir, secure_file
from assistant_bridge.store import JsonStore
from assistant_bridge.summarizer import extract_deadlines, summarize_text
from assistant_bridge.tokens import TokenStore, hash_phrase
from assistant_bridge.util import bound_limit, clean_folder, clean_header, new_id, parse_recipients, utcnow
from assistant_bridge.vault import Vault

_DEFAULT_CONFIG = {
    "connector": "mock",
    "send_enabled": False,
    "allowlist": [],
    "command_phrase_hash": None,
    "imap": {
        "host": "",
        "port": 993,
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_ssl": False,
        "username": "",
        "from_address": "",
    },
}

_DEFAULT_STATE = {
    "seen_ids": [],
    "synced_once": False,
    "briefing": None,
    "drafts": [],
    "pending_sends": [],
    "sent": [],
    "extra_inbox": [],
}

_CONFIRM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MAX_ATTACHMENT_BYTES = 8_000_000
_SECRET_PARTS = {".ssh", ".aws", ".gnupg", ".assistant-bridge", ".config", "keychains", "cookies"}
_SECRET_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "credentials.json",
    "master.key",
    "tokens.json",
    "vault.json",
    "accounts.json",
    "oidc.json",
    "cookies.sqlite",
    "login.keychain-db",
}


class Bridge:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.config = JsonStore(self.home / "config.json", _DEFAULT_CONFIG)
        self.state = JsonStore(self.home / "state.json", _DEFAULT_STATE)
        self.vault = Vault(self.home)
        self.tokens = TokenStore(self.home)
        self.accounts = Accounts(self.home)
        self.audit = AuditLog(self.home)
        self.journal = Journal(self.home)
        self.directory = JsonStore(self.home / "directory.json", {"contacts": [], "updated_at": None})

    @classmethod
    def init(cls, home: Path | str | None = None) -> Path:
        import os

        path = resolve_home(home)
        secure_dir(path)
        created_key = False
        if not os.environ.get("ASSISTANT_BRIDGE_MASTER_KEY") and not (path / "master.key").exists():
            write_master_key(path, generate_master_key())
            created_key = True
        if not (path / "vault.json").exists():
            JsonStore(path / "vault.json", {"password": None}).write({"password": None})
        if not (path / "tokens.json").exists():
            JsonStore(path / "tokens.json", {"tokens": []}).write({"tokens": []})
        if not (path / "state.json").exists():
            JsonStore(path / "state.json", _DEFAULT_STATE).write(json.loads(json.dumps(_DEFAULT_STATE)))
        if not (path / "config.json").exists():
            created = json.loads(json.dumps(_DEFAULT_CONFIG))
            if os.environ.get("WEBSITE_SITE_NAME"):
                created["send_enabled"] = True
            JsonStore(path / "config.json", _DEFAULT_CONFIG).write(created)
        audit = path / "audit.jsonl"
        if not audit.exists():
            audit.write_text("", encoding="utf-8")
            secure_file(audit)
        for name in ("vault.json", "tokens.json", "state.json", "config.json"):
            secure_file(path / name)
        if created_key:
            AuditLog(path).record("init", ok=True, detail={"home": str(path)})
        return path

    @classmethod
    def open(cls, home: Path | str | None = None) -> Bridge:
        path = resolve_home(home)
        if not (path / "config.json").exists():
            raise BridgeError("not initialized; run webmail-mcp init", status=500)
        secure_dir(path)
        return cls(path)

    def connector(self):
        kerberos = current_mailbox()
        if kerberos:
            return self.accounts.connector(kerberos)
        return build_connector(self.config.read(), self.vault.load_password(), self.state.read)

    def list_inbox(self, *, limit: int = 20, folder: str = "INBOX", token_id: str | None = None) -> list[dict]:
        capped = bound_limit(limit)
        mailbox = clean_folder(folder)
        messages = _by_date(self.connector().list_inbox(limit=capped, folder=mailbox))
        self.audit.record(
            "list_inbox",
            ok=True,
            token_id=token_id,
            detail={"count": len(messages), "folder": mailbox},
        )
        return [_public_message(item) for item in messages]

    def search_inbox(
        self,
        *,
        text: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        limit: int = 20,
        folder: str = "INBOX",
        token_id: str | None = None,
    ) -> list[dict]:
        capped = bound_limit(limit)
        mailbox = clean_folder(folder)
        messages = _by_date(
            self.connector().search(text=text, sender=sender, subject=subject, limit=capped, folder=mailbox)
        )
        self.audit.record(
            "search_inbox",
            ok=True,
            token_id=token_id,
            detail={"count": len(messages), "folder": mailbox},
        )
        return [_public_message(item) for item in messages]

    def search_sent(
        self,
        *,
        text: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        limit: int = 20,
        token_id: str | None = None,
    ) -> list[dict]:
        """Mail the user sent. INBOX does not hold it."""
        capped = bound_limit(limit)
        mailbox = self.connector()
        folders = choose_sent_folders(mailbox.list_folders())
        found: list[dict] = []
        seen: set[str] = set()
        for folder in folders:
            if any([text, sender, subject]):
                rows = mailbox.search(text=text, sender=sender, subject=subject, limit=capped, folder=folder)
            else:
                rows = mailbox.list_inbox(limit=capped, folder=folder)
            for raw in rows:
                message_id = str(raw.get("id") or "")
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                item = _public_message(raw)
                item["folder"] = folder
                found.append(item)
        messages = _by_date(found)[:capped]
        self.audit.record(
            "search_sent",
            ok=True,
            token_id=token_id,
            detail={"count": len(messages), "folders": folders},
        )
        return messages

    def latest_brief(self, *, limit: int = 10, folder: str = "INBOX", token_id: str | None = None) -> dict:
        messages = self.list_inbox(limit=bound_limit(limit, default=10), folder=folder, token_id=token_id)
        items = []
        for message in messages:
            items.append(
                {
                    "id": message["id"],
                    "from": message["from"],
                    "subject": message["subject"],
                    "date": message["date"],
                    "preview": _preview(message["body"]),
                    "summary": summarize_text(message["subject"], message["body"]),
                }
            )
        self.audit.record("latest_brief", ok=True, token_id=token_id, detail={"count": len(items)})
        return {"messages": items}

    def find_deadlines(self, *, limit: int = 20, folder: str = "INBOX", token_id: str | None = None) -> dict:
        messages = self.list_inbox(limit=bound_limit(limit), folder=folder, token_id=token_id)
        items = []
        for message in messages:
            summary = summarize_text(message["subject"], message["body"])
            if not summary["deadlines"]:
                continue
            items.append(
                {
                    "id": message["id"],
                    "from": message["from"],
                    "subject": message["subject"],
                    "date": message["date"],
                    "deadlines": summary["deadlines"],
                    "asks": summary["asks"],
                    "suggested_action": summary["suggested_action"],
                }
            )
        self.audit.record("find_deadlines", ok=True, token_id=token_id, detail={"matches": len(items)})
        return {"scanned": len(messages), "items": items}

    def list_folders(self, *, token_id: str | None = None) -> list[str]:
        folders = list(self.connector().list_folders())
        self.audit.record("list_folders", ok=True, token_id=token_id, detail={"count": len(folders)})
        return folders

    def get_message(self, message_id: str, *, token_id: str | None = None) -> dict:
        found = self._find_message(message_id)
        self.audit.record("get_message", ok=True, token_id=token_id, detail={"message_id": message_id})
        return found

    def summarize_message(self, message_id: str, *, token_id: str | None = None) -> dict:
        message = self._find_message(message_id)
        summary = summarize_text(message["subject"], message["body"])
        self.audit.record("summarize_message", ok=True, token_id=token_id, detail={"message_id": message_id})
        return {"id": message["id"], "subject": message["subject"], **summary}

    def summarize_input(self, *, message_id: str | None = None, subject: str | None = None, text: str | None = None, token_id: str | None = None) -> dict:
        if message_id:
            return self.summarize_message(message_id, token_id=token_id)
        if text is None:
            raise BridgeError("message_id or text is required")
        summary = summarize_text(subject or "", text)
        self.audit.record("summarize", ok=True, token_id=token_id, detail={"source": "text"})
        return summary

    def outline_reply(self, message_id: str, *, token_id: str | None = None) -> dict:
        message = self._find_message(message_id)
        summary = summarize_text(message["subject"], message["body"])
        lines = [
            "Hi [Name],",
            "",
            f'Thank you for your email about "{message["subject"]}".',
            "",
        ]
        if summary["asks"]:
            for ask in summary["asks"]:
                lines.append("[answer: " + _compact(ask) + "]")
                lines.append("")
        else:
            lines.append("[your reply]")
            lines.append("")
        if summary["deadlines"]:
            lines.append("[note the deadline: " + ", ".join(summary["deadlines"]) + "]")
            lines.append("")
        lines.extend(["Best regards,", "[Your name]", ""])
        subject = message["subject"]
        if not subject.lower().startswith("re:"):
            subject = "Re: " + subject
        outline = {
            "message_id": message["id"],
            "to": [message["from"]] if message["from"] else [],
            "subject": subject,
            "body": "\n".join(lines),
        }
        self.audit.record("outline_reply", ok=True, token_id=token_id, detail={"message_id": message_id})
        return outline

    def compose_mail(self, to, subject: str, body: str, attachments: list[str] | None = None, *, token_id: str | None = None) -> dict:
        draft = {
            "id": new_id("draft"),
            "kind": "new",
            "message_id": None,
            "to": parse_recipients(to),
            "subject": (subject or "").strip(),
            "body": body or "",
            "attachments": [str(item) for item in attachments or []],
            "created_at": utcnow(),
            "status": "open",
        }
        draft["subject"] = clean_header(draft["subject"], "subject")
        if not draft["subject"]:
            raise BridgeError("subject is required")
        self._save_draft_record(draft)
        self.audit.record("compose_mail", ok=True, token_id=token_id, detail={"draft_id": draft["id"], "kind": "new"})
        self._log_action(
            kind="compose",
            tool="compose_mail",
            token_id=token_id,
            draft_id=draft["id"],
            to=draft["to"],
            subject=draft["subject"],
            status="drafted",
            summary=f"Drafted mail to {', '.join(draft['to'])} about {draft['subject']}",
            body=draft["body"],
        )
        return draft

    def save_draft(
        self,
        message_id: str,
        body: str,
        subject: str | None = None,
        attachments: list[str] | None = None,
        *,
        token_id: str | None = None,
    ) -> dict:
        message = self._find_message(message_id)
        reply_subject = clean_header(subject or "", "subject")
        if not reply_subject:
            reply_subject = clean_header(message["subject"], "subject")
            if not reply_subject.lower().startswith("re:"):
                reply_subject = "Re: " + reply_subject
        draft = {
            "id": new_id("draft"),
            "kind": "reply",
            "message_id": message["id"],
            "to": [message["from"]] if message["from"] else [],
            "subject": reply_subject,
            "body": body or "",
            "attachments": [str(item) for item in attachments or []],
            "created_at": utcnow(),
            "status": "open",
        }
        self._save_draft_record(draft)
        self.audit.record(
            "save_draft",
            ok=True,
            token_id=token_id,
            detail={"draft_id": draft["id"], "message_id": message["id"], "kind": "reply"},
        )
        self._log_action(
            kind="reply",
            tool="save_draft",
            token_id=token_id,
            message_id=message["id"],
            draft_id=draft["id"],
            to=draft["to"],
            subject=draft["subject"],
            status="drafted",
            summary=f"Reply drafted to {', '.join(draft['to'])} about {message['subject']}",
            body=draft["body"],
        )
        return draft

    def list_drafts(self, *, token_id: str | None = None) -> list[dict]:
        drafts = list(self.state.read()["drafts"])
        self.audit.record("list_drafts", ok=True, token_id=token_id, detail={"count": len(drafts)})
        return drafts

    def check_text(self, text: str, message_id: str | None = None, *, token_id: str | None = None) -> dict:
        source = None
        if message_id:
            source = self._find_message(message_id)["body"]
        result = check_draft(text, source)
        self.audit.record(
            "check_text",
            ok=result["ok"],
            token_id=token_id,
            detail={"message_id": message_id, "blocking": len(result["blocking"]), "warnings": len(result["warnings"])},
        )
        return result

    def sync_inbox(self, *, token_id: str | None = None) -> dict:
        messages = [_public_message(item) for item in self.connector().list_inbox(limit=40)]
        ids = [item["id"] for item in messages]
        state = self.state.read()
        if not state.get("synced_once"):
            state["seen_ids"] = ids
            state["synced_once"] = True
            briefing = {"generated_at": utcnow(), "first_sync": True, "new_count": 0, "new": []}
            state["briefing"] = briefing
            self.state.write(state)
            self.audit.record("sync", ok=True, token_id=token_id, detail={"new_count": 0, "first_sync": True})
            return briefing
        seen = set(state.get("seen_ids") or [])
        fresh = [item for item in messages if item["id"] not in seen]
        merged: list[str] = []
        for item_id in [*(state.get("seen_ids") or []), *ids]:
            if item_id not in merged:
                merged.append(item_id)
        state["seen_ids"] = merged
        items = []
        for item in fresh:
            items.append(
                {
                    "id": item["id"],
                    "from": item["from"],
                    "subject": item["subject"],
                    "date": item["date"],
                    "summary": summarize_text(item["subject"], item["body"]),
                }
            )
        briefing = {
            "generated_at": utcnow(),
            "first_sync": False,
            "new_count": len(items),
            "new": items,
            "followups": self._followups(messages, token_id=token_id)["waiting"],
        }
        state["briefing"] = briefing
        self.state.write(state)
        self.audit.record("sync", ok=True, token_id=token_id, detail={"new_count": len(items), "first_sync": False})
        return briefing

    def get_briefing(self, *, token_id: str | None = None) -> dict:
        briefing = self.state.read().get("briefing")
        if not briefing:
            briefing = {"generated_at": None, "first_sync": False, "new_count": 0, "new": [], "note": "No sync yet"}
        self.audit.record("get_briefing", ok=True, token_id=token_id, detail={"new_count": briefing.get("new_count", 0)})
        return briefing

    def probe_mailbox(self, *, token_id: str | None = None) -> dict:
        result = self.connector().probe()
        result.pop("password", None)
        self.audit.record(
            "probe",
            ok=bool(result.get("ok")),
            token_id=token_id,
            detail={"connector": result.get("connector"), "ok": bool(result.get("ok"))},
        )
        return result

    def request_send(self, draft_id: str, *, token_id: str | None = None) -> dict:
        sent = self._send_draft(draft_id, token_id=token_id, action="request_send")
        return sent

    def approve(self, request_id: str) -> dict:
        self._require_send_enabled()
        state = self.state.read()
        request = _find(state["pending_sends"], request_id, "send request not found")
        if request["status"] != "pending":
            raise BridgeError("send request is not pending")
        self._check_allowlist(request["to"])
        checked = self._require_clean(request["body"], request.get("message_id"))
        code = "AB-" + "".join(secrets.choice(_CONFIRM_ALPHABET) for _ in range(6))
        delivery = self._deliver(request["to"], request["subject"], request["body"])
        sent = {
            "id": new_id("sent"),
            "request_id": request["id"],
            "draft_id": request["draft_id"],
            "to": list(request["to"]),
            "subject": request["subject"],
            "body": request["body"],
            "sent_at": utcnow(),
            **delivery,
        }
        request["status"] = "sent"
        request["sent_at"] = sent["sent_at"]
        request["confirm_code_hash"] = hash_phrase(code)
        request["sent_id"] = sent["id"]
        state["sent"].append({key: value for key, value in sent.items() if key != "body"})
        state["sent"][-1]["body"] = sent["body"]
        _mark_draft(state, request["draft_id"], "sent")
        self.state.write(state)
        self.audit.record("approve", ok=True, detail={"request_id": request_id, "status": "sent", "sent_id": sent["id"]})
        return {
            "id": request_id,
            "status": "sent",
            "confirm_code": code,
            "to": sent["to"],
            "subject": sent["subject"],
            "sent_id": sent["id"],
            "warnings": checked["warnings"],
            "via": delivery["via"],
        }

    def confirm_send(self, request_id: str, confirm_code: str, *, token_id: str | None = None) -> dict:
        state = self.state.read()
        request = _find(state["pending_sends"], request_id, "send request not found")
        if request["status"] != "sent" or not request.get("confirm_code_hash"):
            raise BridgeError("waiting for local approval", status=409)
        digest = hash_phrase(confirm_code or "")
        if not hmac.compare_digest(digest, request["confirm_code_hash"]):
            self.audit.record("confirm_send", ok=False, token_id=token_id, detail={"request_id": request_id, "reason": "rejected"})
            raise BridgeError("confirm code rejected", status=403)
        self.audit.record("confirm_send", ok=True, token_id=token_id, detail={"request_id": request_id, "status": "sent"})
        return {
            "id": request_id,
            "status": "sent",
            "sent_id": request.get("sent_id"),
            "to": request["to"],
            "subject": request["subject"],
        }

    def send_now(self, draft_id: str, phrase: str = "", *, token_id: str | None = None) -> dict:
        return self._send_draft(draft_id, token_id=token_id, action="send_now")

    def list_audit(self, limit: int = 50, *, token_id: str | None = None) -> list[dict]:
        rows = self.audit.read_recent(limit)
        self.audit.record("list_audit", ok=True, token_id=token_id, detail={"count": len(rows)})
        return rows

    def pending(self) -> list[dict]:
        rows = []
        for request in self.state.read()["pending_sends"]:
            rows.append(
                {
                    "id": request["id"],
                    "draft_id": request["draft_id"],
                    "status": request["status"],
                    "to": request["to"],
                    "subject": request["subject"],
                    "created_at": request["created_at"],
                }
            )
        return rows

    def enable_send(self) -> dict:
        return self._set_send(True)

    def disable_send(self) -> dict:
        return self._set_send(False)

    def allow_recipient(self, email: str) -> dict:
        address = (email or "").strip()
        if "@" not in address:
            raise BridgeError("recipient must be an email address")
        cfg = self.config.read()
        current = [item.lower() for item in cfg.get("allowlist") or []]
        if address.lower() not in current:
            current.append(address.lower())
        cfg["allowlist"] = current
        self.config.write(cfg)
        self.audit.record("allow_recipient", ok=True, detail={"count": len(current)})
        return {"allowlist": current}

    def set_command_phrase(self, phrase: str) -> None:
        cleaned = (phrase or "").strip()
        if len(cleaned) < 8:
            raise BridgeError("command phrase must be at least 8 characters")
        cfg = self.config.read()
        cfg["command_phrase_hash"] = hash_phrase(cleaned)
        self.config.write(cfg)
        self.audit.record("set_command_phrase", ok=True, detail={"set": True})

    def configure_mock(self) -> dict:
        cfg = self.config.read()
        cfg["connector"] = "mock"
        self.config.write(cfg)
        self.vault.clear_password()
        self.audit.record("connector_mock", ok=True, detail={"connector": "mock"})
        return {"connector": "mock"}

    def configure_imap(
        self,
        *,
        host: str,
        port: int,
        smtp_host: str,
        smtp_port: int,
        smtp_ssl: bool,
        username: str,
        from_address: str,
        password: str,
    ) -> dict:
        reject_webmail(host)
        reject_webmail(smtp_host)
        if not password:
            raise BridgeError("password is required")
        settings = {
            "host": host.strip(),
            "port": int(port),
            "smtp_host": smtp_host.strip(),
            "smtp_port": int(smtp_port),
            "smtp_ssl": bool(smtp_ssl or int(smtp_port) == 465),
            "username": username.strip(),
            "from_address": (from_address or username).strip(),
        }
        if not settings["host"] or not settings["username"] or not settings["smtp_host"]:
            raise BridgeError("host, smtp host, and username are required")
        cfg = self.config.read()
        cfg["connector"] = "imap"
        cfg["imap"] = settings
        self.config.write(cfg)
        self.vault.store_password(password)
        self.audit.record("connector_imap", ok=True, detail={"connector": "imap", "host": settings["host"], "username": settings["username"]})
        return {"connector": "imap", "imap": settings}

    def configure_iitd(self, username: str, password: str) -> dict:
        if not password:
            raise BridgeError("password is required")
        settings = iitd_settings(username)
        cfg = self.config.read()
        cfg["connector"] = "iitd"
        cfg["imap"] = settings
        self.config.write(cfg)
        self.vault.store_password(password)
        self.audit.record(
            "connector_iitd",
            ok=True,
            detail={"connector": "iitd", "host": settings["host"], "username": settings["username"]},
        )
        return {"connector": "iitd", "imap": settings}

    def add_mock_message(self, message: dict) -> dict:
        if self.config.read().get("connector") != "mock":
            raise BridgeError("extra messages are only for the mock connector")
        if not message.get("id"):
            raise BridgeError("id is required")
        normalized = _public_message(message)
        state = self.state.read()
        state["extra_inbox"].append(normalized)
        self.state.write(state)
        return normalized

    def status(self) -> dict:
        cfg = self.config.read()
        tokens = self.tokens.public_list()
        pending = [item for item in self.state.read()["pending_sends"] if item["status"] == "pending"]
        return {
            "home": str(self.home),
            "connector": cfg.get("connector"),
            "send_enabled": bool(cfg.get("send_enabled")),
            "command_phrase_set": bool(cfg.get("command_phrase_hash")),
            "allowlist": list(cfg.get("allowlist") or []),
            "imap": dict(cfg.get("imap") or {}),
            "password_set": self.vault.has_password(),
            "tokens_active": len([item for item in tokens if not item["revoked"]]),
            "pending_sends": len(pending),
        }

    def resolve_person(self, query: str, *, token_id: str | None = None) -> dict:
        result = lookup(self.directory.read().get("contacts") or [], query)
        self.audit.record("resolve_person", ok=True, token_id=token_id, detail={"matches": len(result["matches"])})
        return result

    def learn_contacts(self, *, limit: int = 40, token_id: str | None = None) -> dict:
        incoming = []
        for message in self.connector().list_inbox(limit=bound_limit(limit)):
            person = person_from_header(message.get("from") or "")
            if person:
                incoming.append(person)
        return self._store_contacts(incoming, token_id=token_id, source="mailbox")

    def refresh_directory(self, *, token_id: str | None = None) -> dict:
        incoming = []
        errors = []
        for department, url in FACULTY_PAGES:
            if not url.startswith("https://") or not _faculty_host(url):
                errors.append({"department": department, "error": "refused"})
                continue
            try:
                incoming.extend(ingest_html(fetch_public_page(url), department))
            except Exception as exc:
                errors.append({"department": department, "error": type(exc).__name__})
        stored = self._store_contacts(incoming, token_id=token_id, source="directory")
        stored["errors"] = errors
        return stored

    def ingest_directory_page(self, html: str, department: str, *, token_id: str | None = None) -> dict:
        return self._store_contacts(ingest_html(html, department), token_id=token_id, source="directory")

    def get_thread(self, message_id: str, *, token_id: str | None = None) -> dict:
        anchor = self._find_message(message_id)
        key = normalize_subject(anchor["subject"])
        rows = []
        if key:
            rows.extend(self.connector().search(subject=key, limit=40))
        if not any(str(item.get("id")) == anchor["id"] for item in rows):
            rows.append(anchor)
        messages = []
        seen = set()
        for item in rows:
            public = _public_message(item) if "body" in item else item
            if public["id"] in seen:
                continue
            if normalize_subject(public["subject"]).lower() != key.lower():
                continue
            seen.add(public["id"])
            messages.append(_thread_row(public, "inbox"))
        for sent in self.state.read().get("sent") or []:
            if normalize_subject(sent.get("subject") or "").lower() != key.lower():
                continue
            if sent["id"] in seen:
                continue
            seen.add(sent["id"])
            messages.append(
                {
                    "id": sent["id"],
                    "from": sent.get("from") or "",
                    "to": sent.get("to") or [],
                    "subject": sent.get("subject") or "",
                    "date": sent.get("sent_at") or "",
                    "preview": _preview(sent.get("body") or "", 180),
                    "source": "sent",
                }
            )
        messages.sort(key=lambda item: item.get("date") or "")
        self.audit.record("get_thread", ok=True, token_id=token_id, detail={"message_id": message_id, "count": len(messages)})
        return {"subject": key, "count": len(messages), "messages": messages}

    def list_followups(self, *, days: int = 3, today: date | None = None, token_id: str | None = None) -> dict:
        messages = [_public_message(item) for item in self.connector().list_inbox(limit=40)]
        result = self._followups(messages, days=days, today=today, token_id=token_id)
        self.audit.record(
            "list_followups",
            ok=True,
            token_id=token_id,
            detail={"waiting": len(result["waiting"]), "due_tomorrow": len(result["due_tomorrow"])},
        )
        return result

    def read_attachment(self, message_id: str, filename: str, *, token_id: str | None = None) -> dict:
        found = self.connector().read_attachment(message_id, filename)
        data = found.get("data") or b""
        if len(data) > _MAX_ATTACHMENT_BYTES:
            raise BridgeError("attachment too large")
        text = found.get("text")
        if text is None:
            text = attachment_text(found["filename"], found.get("content_type") or "", data)
        if not text:
            raise BridgeError("no text could be read from that attachment", status=422)
        self.audit.record(
            "read_attachment",
            ok=True,
            token_id=token_id,
            detail={"message_id": message_id, "filename": filename},
        )
        return {"message_id": message_id, "filename": found["filename"], "content_type": found.get("content_type"), "text": text[:20000]}

    def extract_calendar_event(self, message_id: str, *, today: date | None = None, token_id: str | None = None) -> dict:
        message = self._find_message(message_id)
        event = extract_event(message["subject"], message["body"], today=today)
        event["message_id"] = message["id"]
        self.audit.record("extract_event", ok=True, token_id=token_id, detail={"message_id": message_id, "start": event["start"]})
        return event

    def add_calendar_event(
        self,
        *,
        message_id: str | None = None,
        title: str | None = None,
        start: str | None = None,
        location: str | None = None,
        url: str | None = None,
        notes: str | None = None,
        apply_local: bool = True,
        today: date | None = None,
        token_id: str | None = None,
    ) -> dict:
        extracted = {}
        if message_id:
            extracted = self.extract_calendar_event(message_id, today=today, token_id=token_id)
        event_start = start or extracted.get("start")
        if not event_start:
            raise BridgeError("no date found to add; say the day and time")
        event = {
            "id": new_id("event"),
            "message_id": message_id,
            "title": (title or extracted.get("title") or "Campus event").strip(),
            "start": event_start,
            "all_day": bool(extracted.get("all_day")) and not start and not _has_clock(event_start),
            "location": location if location is not None else (extracted.get("location") or ""),
            "url": url if url is not None else (extracted.get("url") or ""),
            "notes": notes or "",
        }
        if "T" in event["start"] and not event["start"].endswith("Z") and "+" not in event["start"][10:]:
            event["all_day"] = False
        folder = self.home / "events"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / f"{event['id']}.ics"
        path.write_text(write_ics(event), encoding="utf-8")
        secure_file(path)
        local = {"added": False, "where": ""}
        if apply_local:
            try:
                local = {"added": True, "where": add_mac_event(event)}
            except Exception as exc:
                local = {"added": False, "where": "", "error": str(exc)[:240]}
        record = {**event, "ics": str(path), "google_url": google_template(event), "calendar": local}
        state = self.state.read()
        state.setdefault("events", []).append({key: value for key, value in record.items() if key != "notes"})
        self.state.write(state)
        self.audit.record("add_calendar_event", ok=True, token_id=token_id, detail={"event_id": event["id"], "added": local["added"]})
        self._log_action(
            kind="calendar",
            tool="add_calendar_event",
            token_id=token_id,
            message_id=message_id,
            subject=event["title"],
            status="added" if local["added"] else "saved",
            summary=f"Calendar event {event['title']} at {event['start']}",
        )
        return record

    def list_actions(self, *, kind: str | None = None, limit: int = 50, token_id: str | None = None) -> list[dict]:
        rows = self.journal.read_recent(limit, kind or None)
        self.audit.record("list_actions", ok=True, token_id=token_id, detail={"count": len(rows), "kind": kind or ""})
        return rows

    def get_action(self, action_id: str, *, token_id: str | None = None) -> dict:
        found = self.journal.get(action_id)
        if found is None:
            raise BridgeError("action not found", status=404)
        self.audit.record("get_action", ok=True, token_id=token_id, detail={"action_id": action_id})
        return found

    def note_tool(self, name: str, arguments: dict, payload, *, ok: bool, token_id: str | None) -> None:
        if name in {"compose_mail", "save_draft", "request_send", "send_now", "add_calendar_event"}:
            return
        summary = name.replace("_", " ")
        message_id = None
        subject = ""
        if isinstance(payload, dict):
            message_id = payload.get("message_id") or payload.get("id")
            subject = payload.get("subject") or payload.get("headline") or ""
            if "messages" in payload:
                summary = f"{name} returned {len(payload['messages'])}"
            elif "count" in payload:
                summary = f"{name} returned {payload['count']}"
        elif isinstance(arguments, dict):
            message_id = arguments.get("message_id")
        self._log_action(
            kind="tool",
            tool=name,
            ok=ok,
            token_id=token_id,
            message_id=str(message_id) if message_id else None,
            subject=str(subject)[:180],
            status="ok" if ok else "failed",
            summary=summary[:180],
        )

    def _store_contacts(self, incoming: list[dict], *, token_id: str | None, source: str) -> dict:
        current = self.directory.read()
        merged = merge_contacts(current.get("contacts") or [], incoming)
        current["contacts"] = merged
        current["updated_at"] = utcnow()
        self.directory.write(current)
        secure_file(self.directory.path)
        self.audit.record("directory", ok=True, token_id=token_id, detail={"source": source, "count": len(merged)})
        return {"count": len(merged), "added": len(incoming), "updated_at": current["updated_at"]}

    def _followups(self, messages: list[dict], *, days: int = 3, today: date | None = None, token_id: str | None = None) -> dict:
        current = today or datetime.now(IST).date()
        wait = max(1, min(int(days or 3), 30))
        cutoff = current - timedelta(days=wait)
        waiting = []
        for sent in self.state.read().get("sent") or []:
            sent_on = _sent_day(sent.get("sent_at") or "")
            if sent_on is None or sent_on > cutoff:
                continue
            key = normalize_subject(sent.get("subject") or "").lower()
            recipients = {item.lower() for item in sent.get("to") or []}
            replied = False
            for message in messages:
                sender = (message.get("from") or "").lower()
                if not any(recipient in sender for recipient in recipients):
                    continue
                if normalize_subject(message.get("subject") or "").lower() != key:
                    continue
                message_day = _sent_day(message.get("date") or "")
                if message_day and message_day >= sent_on:
                    replied = True
                    break
            if not replied:
                waiting.append(
                    {
                        "sent_id": sent.get("id"),
                        "to": sent.get("to") or [],
                        "subject": sent.get("subject") or "",
                        "sent_at": sent.get("sent_at"),
                        "waiting_days": (current - sent_on).days,
                        "message_id": sent.get("message_id"),
                    }
                )
        due = []
        tomorrow = current + timedelta(days=1)
        for message in messages:
            for deadline in extract_deadlines(f"{message.get('subject') or ''}\n{message.get('body') or ''}"):
                parsed = parse_loose_date(deadline, current)
                if parsed == tomorrow:
                    due.append(
                        {
                            "id": message.get("id"),
                            "subject": message.get("subject") or "",
                            "from": message.get("from") or "",
                            "deadline": deadline,
                            "due": tomorrow.isoformat(),
                        }
                    )
                    break
        return {"as_of": current.isoformat(), "waiting": waiting, "due_tomorrow": due}

    def _load_attachments(self, paths: list[str]) -> list[dict]:
        loaded = []
        home = self.home.resolve()
        for raw in paths:
            path = Path(raw).expanduser().resolve()
            if not path.is_file():
                raise BridgeError(f"attachment not found: {path.name}")
            _refuse_secret_attachment(path, home)
            size = path.stat().st_size
            if size > _MAX_ATTACHMENT_BYTES:
                raise BridgeError(f"attachment too large: {path.name}")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            loaded.append({"filename": path.name, "data": path.read_bytes(), "content_type": content_type})
        return loaded

    def _log_action(self, **fields) -> dict:
        return self.journal.record(**fields)

    def _find_message(self, message_id: str) -> dict:
        connector = self.connector()
        found = connector.fetch(message_id)
        if found:
            return _public_message(found)
        for item in connector.list_inbox(limit=40):
            if str(item.get("id")) == message_id:
                return _public_message(item)
        raise BridgeError("message not found", status=404)

    def _save_draft_record(self, draft: dict) -> None:
        state = self.state.read()
        state["drafts"].append(draft)
        self.state.write(state)

    def _open_draft(self, draft_id: str) -> dict:
        for draft in self.state.read()["drafts"]:
            if draft["id"] == draft_id:
                if draft.get("status") == "sent":
                    raise BridgeError("draft already sent")
                return draft
        raise BridgeError("draft not found", status=404)

    def _require_send_enabled(self) -> None:
        if not self.config.read().get("send_enabled"):
            raise BridgeError("send is disabled", status=403)

    def _check_allowlist(self, recipients: list[str]) -> None:
        allowlist = [item.lower() for item in self.config.read().get("allowlist") or []]
        if not allowlist:
            return
        blocked = [item for item in recipients if item.lower() not in allowlist]
        if blocked:
            raise BridgeError("recipient not allowed: " + ", ".join(blocked), status=403)

    def _send_draft(self, draft_id: str, *, token_id: str | None, action: str) -> dict:
        self._require_send_enabled()
        draft = self._open_draft(draft_id)
        self._check_allowlist(draft["to"])
        checked = self._require_clean(draft["body"], draft.get("message_id"), allow_short=True)
        files = self._load_attachments(draft.get("attachments") or [])
        delivery = self._deliver(draft["to"], draft["subject"], draft["body"], files)
        sent = {
            "id": new_id("sent"),
            "request_id": None,
            "draft_id": draft["id"],
            "kind": draft.get("kind") or "new",
            "message_id": draft.get("message_id"),
            "to": list(draft["to"]),
            "subject": draft["subject"],
            "body": draft["body"],
            "sent_at": utcnow(),
            "header_id": delivery.get("message_id") or "",
            "sent_proof": delivery.get("sent_proof") or "",
            "sent_folder": delivery.get("sent_folder") or "",
            **{key: value for key, value in delivery.items() if key in {"via", "from"}},
        }
        state = self.state.read()
        state["sent"].append(sent)
        _mark_draft(state, draft["id"], "sent")
        for request in state["pending_sends"]:
            if request["draft_id"] == draft["id"] and request["status"] == "pending":
                request["status"] = "superseded"
        self.state.write(state)
        self.audit.record(action, ok=True, token_id=token_id, detail={"draft_id": draft_id, "sent_id": sent["id"], "status": "sent"})
        kind = "reply" if draft.get("kind") == "reply" else "send"
        self._log_action(
            kind=kind,
            tool=action,
            token_id=token_id,
            message_id=draft.get("message_id"),
            draft_id=draft["id"],
            sent_id=sent["id"],
            to=sent["to"],
            subject=sent["subject"],
            status="sent",
            summary=f"Sent {kind} to {', '.join(sent['to'])} about {sent['subject']}",
            body=sent["body"],
            sent_proof=sent["sent_proof"],
        )
        return {
            "id": sent["id"],
            "status": "sent",
            "kind": draft.get("kind") or "new",
            "message_id": draft.get("message_id"),
            "draft_id": draft_id,
            "to": sent["to"],
            "subject": sent["subject"],
            "warnings": checked["warnings"],
            "via": delivery["via"],
            "sent_proof": sent["sent_proof"],
            "sent_folder": sent["sent_folder"],
            "header_id": sent["header_id"],
            "attachments": delivery.get("attachments") or [],
        }

    def _require_clean(self, body: str, message_id: str | None, *, allow_short: bool = False) -> dict:
        source = None
        if message_id:
            try:
                source = self._find_message(message_id)["body"]
            except BridgeError:
                source = None
        checked = check_draft(body, source)
        blocking = [item for item in checked["blocking"] if not (allow_short and item == "too short")]
        if blocking:
            raise BridgeError("draft has blocking issues: " + "; ".join(blocking), status=422)
        return {**checked, "blocking": blocking, "ok": not blocking}

    def _deliver(self, to: list[str], subject: str, body: str, attachments: list[dict] | None = None) -> dict:
        cfg = self.config.read()
        from_addr = (cfg.get("imap") or {}).get("from_address") or "student@example.edu"
        if cfg.get("connector") == "mock":
            from_addr = from_addr or "student@example.edu"
        result = self.connector().send(
            to=to,
            subject=subject,
            body=body,
            from_addr=from_addr,
            attachments=attachments or None,
        )
        return {
            "via": result["via"],
            "from": result["from"],
            "message_id": result.get("message_id") or "",
            "sent_proof": result.get("sent_proof") or "",
            "sent_folder": result.get("sent_folder") or "",
            "attachments": list(result.get("attachments") or []),
        }

    def _set_send(self, enabled: bool) -> dict:
        cfg = self.config.read()
        cfg["send_enabled"] = enabled
        self.config.write(cfg)
        action = "enable_send" if enabled else "disable_send"
        self.audit.record(action, ok=True, detail={"send_enabled": enabled})
        return {"send_enabled": enabled}


def assert_local_bind(host: str) -> str:
    import os

    normalized = (host or "").strip().lower()
    # Azure App Service routes to the container. The laptop still refuses a public bind.
    on_azure = bool(os.environ.get("WEBSITE_SITE_NAME"))
    if on_azure and normalized in {"0.0.0.0", "::", "::0"}:
        return "0.0.0.0"
    if normalized in {"0.0.0.0", "::", "::0", ""}:
        raise BridgeError("Refusing to bind 0.0.0.0; webmail-mcp listens on 127.0.0.1 only")
    if normalized not in {"127.0.0.1", "localhost", "::1"}:
        raise BridgeError("Refusing non-local bind; webmail-mcp listens on 127.0.0.1 only")
    return "127.0.0.1"


def _public_message(raw: dict) -> dict:
    to = raw.get("to") or []
    if isinstance(to, str):
        to = [to]
    return {
        "id": str(raw["id"]),
        "from": raw.get("from") or "",
        "to": list(to),
        "subject": raw.get("subject") or "",
        "date": raw.get("date") or "",
        "body": raw.get("body") or "",
        "message_header_id": raw.get("message_header_id") or "",
        "in_reply_to": raw.get("in_reply_to") or "",
        "references": raw.get("references") or "",
        "attachments": [
            {"filename": item.get("filename") or "", "content_type": item.get("content_type") or "", "size": item.get("size") or 0}
            for item in (raw.get("attachments") or [])
        ],
    }


def _find(rows: list[dict], row_id: str, missing: str) -> dict:
    for row in rows:
        if row["id"] == row_id:
            return row
    raise BridgeError(missing, status=404)


def _mark_draft(state: dict, draft_id: str, status: str) -> None:
    for draft in state["drafts"]:
        if draft["id"] == draft_id:
            draft["status"] = status


def _compact(text: str) -> str:
    return " ".join(text.split())


def _preview(text: str, size: int = 400) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= size:
        return compact
    return compact[: size - 1].rstrip() + "…"


def _by_date(messages: list[dict]) -> list[dict]:
    return sorted(messages, key=lambda item: item.get("date") or "", reverse=True)


def _thread_row(message: dict, source: str) -> dict:
    return {
        "id": message["id"],
        "from": message.get("from") or "",
        "to": message.get("to") or [],
        "subject": message.get("subject") or "",
        "date": message.get("date") or "",
        "preview": _preview(message.get("body") or "", 180),
        "source": source,
    }


def _sent_day(value: str) -> date | None:
    text = (value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _has_clock(value: str) -> bool:
    return "T" in (value or "")


def _refuse_secret_attachment(path: Path, home: Path) -> None:
    if path == home or home in path.parents:
        raise BridgeError("refusing to attach a file from the bridge data directory")
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    if parts & _SECRET_PARTS or name in _SECRET_NAMES or name.startswith(".env"):
        raise BridgeError("refusing to attach a credential or secret file")


def _faculty_host(url: str) -> bool:
    host = url.split("/", 3)[2].lower()
    return host == "iitd.ac.in" or host.endswith(".iitd.ac.in")
