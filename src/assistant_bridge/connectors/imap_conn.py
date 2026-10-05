from __future__ import annotations

import imaplib
import re
import smtplib
import time
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parsedate_to_datetime

from assistant_bridge.errors import BridgeError
from assistant_bridge.util import bound_limit, clean_folder


class ImapConnector:
    """Generic IMAP4_SSL inbox plus SMTP. INBOX is opened readonly."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        smtp_host: str,
        smtp_port: int,
        smtp_ssl: bool,
        from_addr: str,
        label: str = "imap",
    ):
        self.host = host
        self.port = int(port)
        self.username = username
        self._password = password
        self.smtp_host = smtp_host
        self.smtp_port = int(smtp_port)
        self.smtp_ssl = bool(smtp_ssl)
        self.from_addr = from_addr
        self.label = label

    def __repr__(self) -> str:
        return f"ImapConnector(label={self.label!r}, host={self.host!r}, username={self.username!r})"

    def list_inbox(self, limit: int = 20, folder: str = "INBOX") -> list[dict]:
        return self._collect(folder, limit, ["ALL"])

    def search(
        self,
        *,
        text: str | None = None,
        sender: str | None = None,
        subject: str | None = None,
        limit: int = 20,
        folder: str = "INBOX",
    ) -> list[dict]:
        criteria: list[str] = []
        if sender:
            criteria.extend(["FROM", imap_quoted(sender)])
        if subject:
            criteria.extend(["SUBJECT", imap_quoted(subject)])
        if text:
            criteria.extend(["TEXT", imap_quoted(text)])
        if not criteria:
            raise BridgeError("provide text, from, or subject")
        return self._collect(folder, limit, criteria)

    def fetch(self, message_id: str, folder: str = "INBOX") -> dict | None:
        if not str(message_id).startswith("uid:"):
            return None
        uid = str(message_id).split(":", 1)[1]
        if not uid.isdigit():
            raise BridgeError("invalid message id")
        with self._imap() as imap:
            imap.select(clean_folder(folder), readonly=True)
            try:
                return self._fetch(imap, uid.encode())
            except BridgeError:
                return None

    def list_folders(self) -> list[str]:
        with self._imap() as imap:
            status, data = imap.list()
            if status != "OK" or not data:
                return ["INBOX"]
            names = []
            for line in data:
                if not isinstance(line, bytes):
                    continue
                name = _mailbox_name(line)
                if name and name not in names:
                    names.append(name)
            return names or ["INBOX"]

    def _collect(self, folder: str, limit: int, criteria: list[str]) -> list[dict]:
        capped = bound_limit(limit)
        with self._imap() as imap:
            imap.select(clean_folder(folder), readonly=True)
            status, data = imap.uid("search", None, *criteria)
            if status != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-capped:]
            return [self._fetch(imap, uid) for uid in reversed(uids)]

    def send(self, *, to: list[str], subject: str, body: str, from_addr: str, attachments: list[dict] | None = None) -> dict:
        sender = from_addr or self.from_addr
        message = build_outgoing(to=to, subject=subject, body=body, from_addr=sender, attachments=attachments)
        header_id = str(message["Message-ID"])
        try:
            if self.smtp_ssl or self.smtp_port == 465:
                smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
            else:
                smtp = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
                smtp.starttls()
            try:
                smtp.login(self.username, self._password)
                smtp.send_message(message)
            finally:
                try:
                    smtp.quit()
                except smtplib.SMTPException:
                    smtp.close()
        except Exception as exc:
            raise BridgeError("smtp send failed: " + _safe_error(exc, self._password), status=502) from exc
        proof = self._prove_sent(message, header_id)
        return {"via": self.label, "from": sender, "message_id": header_id, **proof}

    def read_attachment(self, message_id: str, filename: str, folder: str = "INBOX") -> dict:
        parsed = self._parsed(message_id, folder)
        if parsed is None:
            raise BridgeError("message not found", status=404)
        wanted = (filename or "").strip().lower()
        for part in parsed.walk():
            current = _header(part.get_filename())
            if not current or current.lower() != wanted:
                continue
            data = part.get_payload(decode=True) or b""
            return {
                "filename": current,
                "content_type": part.get_content_type(),
                "size": len(data),
                "data": data,
            }
        raise BridgeError("attachment not found", status=404)

    def probe(self) -> dict:
        base = {
            "connector": self.label,
            "host": self.host,
            "port": self.port,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "username": self.username,
            "from": self.from_addr,
        }
        try:
            with self._imap() as imap:
                imap.select("INBOX", readonly=True)
                status, data = imap.uid("search", None, "ALL")
                count = len(data[0].split()) if status == "OK" and data and data[0] else 0
            return {"ok": True, "inbox_count": count, **base}
        except Exception as exc:
            return {"ok": False, "error": _safe_error(exc, self._password), **base}

    def _imap(self):
        try:
            imap = imaplib.IMAP4_SSL(self.host, self.port, timeout=30)
        except Exception as exc:
            raise BridgeError("imap connection failed: " + _safe_error(exc, self._password), status=502) from exc
        try:
            imap.login(self.username, self._password)
        except Exception as exc:
            try:
                imap.logout()
            except Exception:
                pass
            raise BridgeError("imap login failed", status=502) from exc
        return _ImapSession(imap)

    def _fetch(self, imap, uid: bytes) -> dict:
        status, data = imap.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not data:
            raise BridgeError("imap fetch failed", status=502)
        raw = b""
        for part in data:
            if isinstance(part, tuple) and len(part) > 1:
                raw = part[1]
                break
        parsed = message_from_bytes(raw)
        uid_text = uid.decode() if isinstance(uid, bytes) else str(uid)
        return _message_dict(parsed, f"uid:{uid_text}")

    def _parsed(self, message_id: str, folder: str):
        if not str(message_id).startswith("uid:"):
            return None
        uid = str(message_id).split(":", 1)[1]
        if not uid.isdigit():
            raise BridgeError("invalid message id")
        with self._imap() as imap:
            imap.select(clean_folder(folder), readonly=True)
            status, data = imap.uid("fetch", uid.encode(), "(RFC822)")
            if status != "OK" or not data:
                return None
            raw = b""
            for part in data:
                if isinstance(part, tuple) and len(part) > 1:
                    raw = part[1]
                    break
            if not raw:
                return None
            return message_from_bytes(raw)

    def _prove_sent(self, message: EmailMessage, header_id: str) -> dict:
        try:
            with self._imap() as imap:
                status, data = imap.list()
                names = []
                if status == "OK" and data:
                    for line in data:
                        if isinstance(line, bytes):
                            name = _mailbox_name(line)
                            if name:
                                names.append(name)
                folders = choose_sent_folders(names)
                found = _search_message_id(imap, folders, header_id)
                if found:
                    return {"sent_proof": "found", "sent_folder": found}
                target = folders[0] if folders else "Sent"
                imap.append(target, "\\Seen", imaplib.Time2Internaldate(time.time()), message.as_bytes())
                found = _search_message_id(imap, [target], header_id)
                if found:
                    return {"sent_proof": "found", "sent_folder": found}
                return {"sent_proof": "not_in_sent", "sent_folder": target}
        except Exception as exc:
            return {"sent_proof": "unchecked", "sent_folder": "", "proof_error": _safe_error(exc, self._password)}


class _ImapSession:
    def __init__(self, imap):
        self.imap = imap

    def __enter__(self):
        return self.imap

    def __exit__(self, exc_type, exc, tb):
        try:
            self.imap.close()
        except Exception:
            pass
        try:
            self.imap.logout()
        except Exception:
            pass
        return False


def imap_quoted(value: str) -> str:
    cleaned = " ".join((value or "").split())[:80]
    cleaned = cleaned.replace("\\", "").replace('"', "")
    if not cleaned:
        raise BridgeError("empty search term")
    return f'"{cleaned}"'


def _mailbox_name(line: bytes) -> str:
    text = line.decode("utf-8", errors="replace")
    quoted = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
    if quoted:
        return quoted[-1].replace('\\"', '"')
    parts = text.split()
    return parts[-1] if parts else ""


def build_outgoing(
    *,
    to: list[str],
    subject: str,
    body: str,
    from_addr: str,
    attachments: list[dict] | None = None,
) -> EmailMessage:
    """Build a message IITD's filter will accept. It rejects mail with no Date header."""
    message = EmailMessage()
    message["From"] = _safe_header(from_addr, "from")
    message["To"] = ", ".join(_safe_header(item, "recipient") for item in to)
    message["Subject"] = _safe_header(subject, "subject")
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(body)
    for item in attachments or []:
        maintype, subtype = (item.get("content_type") or "application/octet-stream").split("/", 1)
        message.add_attachment(item["data"], maintype=maintype, subtype=subtype, filename=item["filename"])
    return message


def _safe_header(value: str, label: str) -> str:
    text = str(value or "")
    if any(char in text for char in "\r\n\x00"):
        raise BridgeError(f"{label} contains a line break")
    return text


def choose_sent_folders(names: list[str]) -> list[str]:
    ranked = []
    for name in names:
        leaf = name.replace("\\", "/").split("/")[-1].lower()
        if leaf in {"sent", "sent items", "sent mail", "sent messages"} or leaf.endswith(".sent"):
            ranked.append(name)
    return ranked or ["Sent"]


def _search_message_id(imap, folders: list[str], header_id: str) -> str | None:
    quoted = imap_quoted(header_id)
    for folder in folders:
        try:
            status, _ = imap.select(folder, readonly=True)
        except imaplib.IMAP4.error:
            continue
        if status != "OK":
            continue
        status, data = imap.uid("search", None, "HEADER", "Message-ID", quoted)
        if status == "OK" and data and data[0]:
            return folder
    return None


def _message_dict(parsed, message_id: str) -> dict:
    return {
        "id": message_id,
        "from": _header(parsed.get("From")),
        "to": [_header(parsed.get("To"))] if parsed.get("To") else [],
        "subject": _header(parsed.get("Subject")),
        "date": _date(parsed.get("Date")),
        "body": _plain_body(parsed),
        "message_header_id": _header(parsed.get("Message-ID")),
        "in_reply_to": _header(parsed.get("In-Reply-To")),
        "references": _header(parsed.get("References")),
        "attachments": _attachment_meta(parsed),
    }


def _attachment_meta(parsed) -> list[dict]:
    rows = []
    for part in parsed.walk():
        filename = _header(part.get_filename())
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        rows.append({"filename": filename, "content_type": part.get_content_type(), "size": len(payload)})
    return rows


def _header(value: str | None) -> str:
    if not value:
        return ""
    chunks = []
    for text, encoding in decode_header(value):
        if isinstance(text, bytes):
            chunks.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            chunks.append(text)
    return "".join(chunks)


def _date(value: str | None) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return value


def _plain_body(parsed) -> str:
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        return ""
    payload = parsed.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(parsed.get_content_charset() or "utf-8", errors="replace")
    return str(parsed.get_payload() or "")


def _safe_error(exc: Exception, secret: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if secret and secret in text:
        text = text.replace(secret, "[redacted]")
    return text[:200]
