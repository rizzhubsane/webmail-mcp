from __future__ import annotations

import re

from assistant_bridge.errors import BridgeError

# Official Thunderbird/Outlook settings published by BSW.
# https://bsw.iitd.ac.in/email.php
# This preset never contacts webmail.iitd.ac.in.
IITD_SETTINGS_URL = "https://bsw.iitd.ac.in/email.php"
IITD_IMAP_HOST = "mailstore.iitd.ac.in"
IITD_IMAP_PORT = 993
IITD_SMTP_HOST = "smtp.iitd.ac.in"
IITD_SMTP_PORT = 465
_KERBEROS = re.compile(r"[A-Za-z0-9._-]+")


def normalize_kerberos(username: str) -> str:
    value = (username or "").strip()
    suffix = "@iitd.ac.in"
    if value.lower().endswith(suffix):
        value = value[: -len(suffix)].strip()
    return value


def iitd_settings(username: str) -> dict:
    user = normalize_kerberos(username)
    if not user or not _KERBEROS.fullmatch(user):
        raise BridgeError("username must be a Kerberos ID; strip @iitd.ac.in if you pasted the email")
    return {
        "host": IITD_IMAP_HOST,
        "port": IITD_IMAP_PORT,
        "smtp_host": IITD_SMTP_HOST,
        "smtp_port": IITD_SMTP_PORT,
        "smtp_ssl": True,
        "username": user,
        "from_address": f"{user}@iitd.ac.in",
    }


def reject_webmail(host: str) -> None:
    if "webmail.iitd.ac.in" in (host or "").lower():
        raise BridgeError("webmail.iitd.ac.in is not used; IMAP host is mailstore.iitd.ac.in")
