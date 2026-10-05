from assistant_bridge.connectors.iitd import iitd_settings, normalize_kerberos, reject_webmail
from assistant_bridge.connectors.imap_conn import ImapConnector
from assistant_bridge.connectors.mock import MockConnector
from assistant_bridge.errors import BridgeError


def build_connector(config: dict, password: str | None, read_state):
    kind = config.get("connector") or "mock"
    if kind == "mock":
        return MockConnector(read_state)
    imap = config.get("imap") or {}
    host = imap.get("host") or ""
    reject_webmail(host)
    reject_webmail(imap.get("smtp_host") or "")
    if not password:
        raise BridgeError("no mailbox password in the vault", status=400)
    if not host or not imap.get("username"):
        raise BridgeError("imap connector is not configured", status=400)
    return ImapConnector(
        host=host,
        port=int(imap.get("port") or 993),
        username=imap["username"],
        password=password,
        smtp_host=imap.get("smtp_host") or "",
        smtp_port=int(imap.get("smtp_port") or 587),
        smtp_ssl=bool(imap.get("smtp_ssl")),
        from_addr=imap.get("from_address") or "",
        label=kind,
    )


__all__ = [
    "ImapConnector",
    "MockConnector",
    "build_connector",
    "iitd_settings",
    "normalize_kerberos",
    "reject_webmail",
]
