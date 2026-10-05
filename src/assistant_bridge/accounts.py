from __future__ import annotations

import contextvars
import imaplib
import json
import time
from pathlib import Path

from assistant_bridge.connectors.imap_conn import ImapConnector
from assistant_bridge.connectors.iitd import iitd_settings
from assistant_bridge.crypto import load_fernet
from assistant_bridge.errors import BridgeError
from assistant_bridge.store import JsonStore
from assistant_bridge.tokens import TokenStore
from assistant_bridge.util import utcnow

SESSION_COOKIE = "wm_session"
_SESSION_SECONDS = 3600
_mailbox: contextvars.ContextVar[str | None] = contextvars.ContextVar("mailbox_kerberos", default=None)


def bind_mailbox(kerberos: str | None) -> None:
    _mailbox.set(kerberos or None)


def current_mailbox() -> str | None:
    return _mailbox.get()


def kerberos_from_profile(profile: dict) -> str:
    raw = str(profile.get("kerberos") or "").strip()
    email = str(profile.get("email") or "").strip()
    if not raw and email.lower().endswith("@iitd.ac.in"):
        raw = email
    if not raw:
        return ""
    try:
        return iitd_settings(raw)["username"]
    except BridgeError:
        return ""


def seal_session(home: Path, profile: dict) -> str:
    kerberos = kerberos_from_profile(profile)
    if not kerberos:
        raise BridgeError("IIT Delhi did not return a Kerberos id. Sign in with IIT Delhi.", status=400)
    payload = {
        "sub": str(profile.get("sub") or ""),
        "kerberos": kerberos,
        "name": str(profile.get("name") or ""),
        "email": str(profile.get("email") or ""),
        "exp": int(time.time()) + _SESSION_SECONDS,
    }
    return load_fernet(home).encrypt(json.dumps(payload).encode()).decode()


def open_session(home: Path, token: str | None) -> dict:
    if not token:
        raise BridgeError("sign in again", status=401)
    try:
        payload = json.loads(load_fernet(home).decrypt(token.encode()).decode())
    except Exception as exc:
        raise BridgeError("sign in again", status=401) from exc
    if int(payload.get("exp") or 0) < time.time():
        raise BridgeError("sign in again", status=401)
    if not payload.get("kerberos"):
        raise BridgeError("sign in again", status=401)
    return payload


def accept_iitd_password(kerberos: str, password: str) -> None:
    settings = iitd_settings(kerberos)
    try:
        imap = imaplib.IMAP4_SSL(settings["host"], settings["port"], timeout=20)
    except Exception as exc:
        raise BridgeError("could not reach IIT Delhi mail", status=502) from exc
    try:
        imap.login(settings["username"], password)
    except Exception as exc:
        raise BridgeError("mail password was not accepted", status=401) from exc
    finally:
        try:
            imap.logout()
        except Exception:
            pass


class Accounts:
    """One encrypted mail password per Kerberos id confirmed by DevClub."""

    def __init__(self, home: Path):
        self.home = home
        self._store = JsonStore(home / "accounts.json", {"users": {}})
        self.tokens = TokenStore(home)

    def has_mail(self, kerberos: str) -> bool:
        user = self._store.read()["users"].get(kerberos) or {}
        return bool(user.get("password"))

    def connect(self, session: dict, password: str, checker=None) -> str | None:
        kerberos = str(session.get("kerberos") or "")
        iitd_settings(kerberos)
        secret = password or ""
        if not secret.strip() or len(secret) > 256:
            raise BridgeError("enter the mail password")
        (checker or accept_iitd_password)(kerberos, secret)
        ciphertext = load_fernet(self.home).encrypt(secret.encode()).decode()
        data = self._store.read()
        users = data.setdefault("users", {})
        current = users.get(kerberos) or {}
        token_id = current.get("token_id")
        raw_token = None
        if not token_id:
            raw_token, record = self.tokens.create(f"mail-{kerberos}", ["read", "draft", "send"])
            token_id = record["id"]
        users[kerberos] = {
            "sub": session.get("sub") or "",
            "name": session.get("name") or "",
            "email": session.get("email") or "",
            "password": ciphertext,
            "token_id": token_id,
            "connected_at": utcnow(),
        }
        self._store.write(data)
        return raw_token

    def kerberos_for_token(self, token_id: str) -> str | None:
        for kerberos, user in self._store.read()["users"].items():
            if user.get("token_id") == token_id and user.get("password"):
                return kerberos
        return None

    def connector(self, kerberos: str) -> ImapConnector:
        user = self._store.read()["users"].get(kerberos) or {}
        ciphertext = user.get("password")
        if not ciphertext:
            raise BridgeError("mail is not connected for this student", status=401)
        password = load_fernet(self.home).decrypt(ciphertext.encode()).decode()
        settings = iitd_settings(kerberos)
        return ImapConnector(
            host=settings["host"],
            port=settings["port"],
            username=settings["username"],
            password=password,
            smtp_host=settings["smtp_host"],
            smtp_port=settings["smtp_port"],
            smtp_ssl=settings["smtp_ssl"],
            from_addr=settings["from_address"],
            label="iitd",
        )
