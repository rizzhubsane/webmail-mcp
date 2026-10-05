import imaplib
import smtplib

import pytest

from assistant_bridge.connectors.iitd import (
    IITD_IMAP_HOST,
    IITD_IMAP_PORT,
    IITD_SMTP_HOST,
    IITD_SMTP_PORT,
    iitd_settings,
    normalize_kerberos,
)
from assistant_bridge.errors import BridgeError


def test_username_strips_campus_domain():
    assert normalize_kerberos("ee1234567@iitd.ac.in") == "ee1234567"
    assert normalize_kerberos("  EE1234567@IITD.AC.IN ") == "EE1234567"
    assert normalize_kerberos("ee1234567") == "ee1234567"
    settings = iitd_settings("ee1234567@iitd.ac.in")
    assert settings["host"] == IITD_IMAP_HOST == "mailstore.iitd.ac.in"
    assert settings["port"] == IITD_IMAP_PORT == 993
    assert settings["smtp_host"] == IITD_SMTP_HOST == "smtp.iitd.ac.in"
    assert settings["smtp_port"] == IITD_SMTP_PORT == 465
    assert settings["smtp_ssl"] is True
    assert settings["username"] == "ee1234567"
    assert settings["from_address"] == "ee1234567@iitd.ac.in"


def test_iitd_preset_encrypts_password_without_connecting(bridge, home, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("tests must not connect to IITD")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse)
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse)
    monkeypatch.setattr(smtplib, "SMTP", refuse)

    secret = "not-a-real-mailbox-password"
    result = bridge.configure_iitd("ee1234567@iitd.ac.in", secret)
    assert result["connector"] == "iitd"
    assert result["imap"]["host"] == "mailstore.iitd.ac.in"
    assert result["imap"]["smtp_port"] == 465

    config_text = (home / "config.json").read_text(encoding="utf-8")
    vault_text = (home / "vault.json").read_text(encoding="utf-8")
    audit_text = (home / "audit.jsonl").read_text(encoding="utf-8")
    assert secret not in config_text
    assert secret not in vault_text
    assert secret not in audit_text
    assert "webmail.iitd.ac.in" not in config_text
    assert bridge.vault.load_password() == secret

    with pytest.raises(BridgeError, match="webmail"):
        bridge.configure_imap(
            host="webmail.iitd.ac.in",
            port=993,
            smtp_host="smtp.iitd.ac.in",
            smtp_port=465,
            smtp_ssl=True,
            username="ee1234567",
            from_address="ee1234567@iitd.ac.in",
            password=secret,
        )
