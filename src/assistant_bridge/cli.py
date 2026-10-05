from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

from dotenv import load_dotenv

from assistant_bridge.errors import BridgeError
from assistant_bridge.mcp_server import serve_stdio
from assistant_bridge.paths import config_dir, resolve_home
from assistant_bridge.service import Bridge, assert_local_bind


def main(argv: list[str] | None = None) -> int:
    load_dotenv(override=False)
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BridgeError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="webmail-mcp", description="Local mail gateway. Credentials stay on this machine.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Create the local data directory")
    init.set_defaults(func=_init)

    token = sub.add_parser("token", help="Create, list, or revoke assistant tokens")
    token_sub = token.add_subparsers(dest="token_cmd", required=True)
    create = token_sub.add_parser("create")
    create.add_argument("--name", default="cursor")
    create.add_argument("--scope", action="append", dest="scopes")
    create.set_defaults(func=_token_create)
    listing = token_sub.add_parser("list")
    listing.set_defaults(func=_token_list)
    revoke = token_sub.add_parser("revoke")
    revoke.add_argument("token_id")
    revoke.set_defaults(func=_token_revoke)

    connector = sub.add_parser("connector", help="Choose mock, imap, or the IIT Delhi preset")
    conn_sub = connector.add_subparsers(dest="connector_cmd", required=True)
    mock = conn_sub.add_parser("mock")
    mock.set_defaults(func=_connector_mock)
    imap = conn_sub.add_parser("imap")
    imap.add_argument("--host", required=True)
    imap.add_argument("--port", type=int, default=993)
    imap.add_argument("--smtp-host", required=True)
    imap.add_argument("--smtp-port", type=int, default=587)
    imap.add_argument("--smtp-ssl", action="store_true")
    imap.add_argument("--username", required=True)
    imap.add_argument("--from", dest="from_address", required=True)
    imap.set_defaults(func=_connector_imap)
    iitd = conn_sub.add_parser("iitd", help="Official mailstore/smtp preset. Password via prompt or env, never chat.")
    iitd.add_argument("--username", required=True, help="Kerberos ID. @iitd.ac.in is stripped if pasted.")
    iitd.set_defaults(func=_connector_iitd)

    for name, func in (
        ("enable-send", _enable_send),
        ("disable-send", _disable_send),
    ):
        cmd = sub.add_parser(name)
        cmd.set_defaults(func=func)

    allow = sub.add_parser("allow-recipient")
    allow.add_argument("email")
    allow.set_defaults(func=_allow)

    phrase = sub.add_parser("set-command-phrase")
    phrase.set_defaults(func=_phrase)

    compose = sub.add_parser("compose")
    compose.add_argument("--to", required=True)
    compose.add_argument("--subject", required=True)
    compose.add_argument("--body", required=True)
    compose.set_defaults(func=_compose)

    drafts = sub.add_parser("drafts")
    drafts.set_defaults(func=_drafts)

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=_serve)

    mcp = sub.add_parser("mcp")
    mcp.set_defaults(func=_mcp)

    inbox = sub.add_parser("inbox")
    inbox.set_defaults(func=_inbox)
    probe = sub.add_parser("probe")
    probe.set_defaults(func=_probe)
    sync = sub.add_parser("sync")
    sync.set_defaults(func=_sync)
    briefing = sub.add_parser("briefing")
    briefing.set_defaults(func=_briefing)

    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=int, default=180)
    watch.add_argument("--once", action="store_true")
    watch.set_defaults(func=_watch)

    pending = sub.add_parser("pending")
    pending.set_defaults(func=_pending)
    approve = sub.add_parser("approve")
    approve.add_argument("request_id")
    approve.set_defaults(func=_approve)
    audit = sub.add_parser("audit")
    audit.add_argument("--limit", type=int, default=50)
    audit.set_defaults(func=_audit)
    actions = sub.add_parser("actions", help="Revisit replies, sends, and other logged actions")
    actions.add_argument("--kind", default="")
    actions.add_argument("--limit", type=int, default=30)
    actions.set_defaults(func=_actions)
    directory = sub.add_parser("directory", help="Local name book")
    directory_sub = directory.add_subparsers(dest="directory_cmd", required=True)
    refresh = directory_sub.add_parser("refresh")
    refresh.set_defaults(func=_directory_refresh)
    who = directory_sub.add_parser("who")
    who.add_argument("query")
    who.set_defaults(func=_directory_who)
    status = sub.add_parser("status")
    status.set_defaults(func=_status)
    export = sub.add_parser("export-openai")
    export.set_defaults(func=_export)
    return parser


def _bridge() -> Bridge:
    return Bridge.open(resolve_home())


def _init(_args) -> int:
    home = Bridge.init(resolve_home())
    print(f"Initialized {home}")
    print("Mailbox passwords stay in the encrypted vault. Do not paste them into chat.")
    return 0


def _token_create(args) -> int:
    scopes = args.scopes or ["read"]
    raw, record = _bridge().tokens.create(args.name, scopes)
    _bridge().audit.record("token_create", ok=True, detail={"token_id": record["id"], "scopes": record["scopes"]})
    print("Token created. Copy it now; it will not be shown again.")
    print(raw)
    print(f"id: {record['id']}")
    print("scopes: " + ", ".join(record["scopes"]))
    return 0


def _token_list(_args) -> int:
    rows = _bridge().tokens.public_list()
    if not rows:
        print("No tokens.")
        return 0
    for row in rows:
        state = "revoked" if row["revoked"] else "active"
        print(f"{row['id']}  {row['name']}  {','.join(row['scopes'])}  {state}  {row['created_at']}")
    return 0


def _token_revoke(args) -> int:
    result = _bridge().tokens.revoke(args.token_id)
    _bridge().audit.record("token_revoke", ok=True, detail={"token_id": result["id"]})
    print(f"Revoked {result['id']}")
    return 0


def _connector_mock(_args) -> int:
    print(json.dumps(_bridge().configure_mock(), indent=2))
    return 0


def _connector_imap(args) -> int:
    password = _read_password()
    result = _bridge().configure_imap(
        host=args.host,
        port=args.port,
        smtp_host=args.smtp_host,
        smtp_port=args.smtp_port,
        smtp_ssl=args.smtp_ssl,
        username=args.username,
        from_address=args.from_address,
        password=password,
    )
    print(json.dumps(result, indent=2))
    print("Password stored encrypted. It was not printed.")
    return 0


def _connector_iitd(args) -> int:
    password = _read_password()
    result = _bridge().configure_iitd(args.username, password)
    print(json.dumps(result, indent=2))
    print("Password stored encrypted in the local vault. It was not printed.")
    print("Settings source: https://bsw.iitd.ac.in/email.php")
    return 0


def _read_password() -> str:
    password = os.environ.get("ASSISTANT_BRIDGE_IMAP_PASSWORD")
    if password:
        return password
    password = getpass.getpass("Mailbox password (stored encrypted locally, not printed): ")
    if not password:
        raise BridgeError("password is required")
    return password


def _enable_send(_args) -> int:
    print(json.dumps(_bridge().enable_send()))
    return 0


def _disable_send(_args) -> int:
    print(json.dumps(_bridge().disable_send()))
    return 0


def _allow(args) -> int:
    print(json.dumps(_bridge().allow_recipient(args.email), indent=2))
    return 0


def _phrase(_args) -> int:
    first = getpass.getpass("Command phrase (hidden): ")
    second = getpass.getpass("Repeat command phrase: ")
    if first != second:
        raise BridgeError("phrases did not match")
    _bridge().set_command_phrase(first)
    print("Command phrase saved as a hash. It will not be shown again. Do not invent a phrase in chat.")
    return 0


def _compose(args) -> int:
    draft = _bridge().compose_mail(args.to, args.subject, args.body)
    print(json.dumps(draft, indent=2))
    return 0


def _drafts(_args) -> int:
    print(json.dumps(_bridge().list_drafts(), indent=2))
    return 0


def _serve(args) -> int:
    host = assert_local_bind(args.host)
    import logging

    import uvicorn

    from assistant_bridge.audit import redact_log
    from assistant_bridge.http_api import create_app

    class _Redact(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if isinstance(record.args, tuple):
                record.args = tuple(redact_log(item) if isinstance(item, str) else item for item in record.args)
            if isinstance(record.msg, str):
                record.msg = redact_log(record.msg)
            return True

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(_Redact())
    home = resolve_home()
    if not (home / "config.json").exists():
        Bridge.init(home)
    bridge = Bridge.open(home)
    where = "Azure" if os.environ.get("WEBSITE_SITE_NAME") else "localhost only"
    print(f"webmail-mcp http://{host}:{args.port}  ({where})", file=sys.stderr)
    uvicorn.run(create_app(bridge), host=host, port=args.port, log_level="info")
    return 0


def _mcp(_args) -> int:
    serve_stdio(_bridge())
    return 0


def _inbox(_args) -> int:
    for message in _bridge().list_inbox():
        print(f"{message['id']}\t{message['date']}\t{message['from']}\t{message['subject']}")
    return 0


def _probe(_args) -> int:
    result = _bridge().probe_mailbox()
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def _sync(_args) -> int:
    print(json.dumps(_bridge().sync_inbox(), indent=2))
    return 0


def _briefing(_args) -> int:
    print(json.dumps(_bridge().get_briefing(), indent=2))
    return 0


def _watch(args) -> int:
    if args.interval < 1:
        raise BridgeError("interval must be at least 1 second")
    interval = args.interval
    bridge = _bridge()
    while True:
        print(json.dumps(bridge.sync_inbox(), indent=2))
        if args.once:
            return 0
        time.sleep(interval)


def _pending(_args) -> int:
    rows = _bridge().pending()
    if not rows:
        print("No send requests.")
        return 0
    print(json.dumps(rows, indent=2))
    return 0


def _approve(args) -> int:
    result = _bridge().approve(args.request_id)
    code = result["confirm_code"]
    printable = {key: value for key, value in result.items() if key != "confirm_code"}
    print(json.dumps(printable, indent=2))
    print(f"Confirm code: {code}")
    print("Give this code to the assistant only if you want it to verify the send.")
    print("Do not let the assistant invent a code.")
    return 0


def _audit(args) -> int:
    print(json.dumps(_bridge().list_audit(args.limit), indent=2))
    return 0


def _actions(args) -> int:
    print(json.dumps(_bridge().list_actions(kind=args.kind or None, limit=args.limit), indent=2))
    return 0


def _directory_refresh(_args) -> int:
    result = _bridge().refresh_directory()
    print(json.dumps({"count": result["count"], "errors": result.get("errors") or []}, indent=2))
    return 0


def _directory_who(args) -> int:
    print(json.dumps(_bridge().resolve_person(args.query), indent=2))
    return 0


def _status(_args) -> int:
    print(json.dumps(_bridge().status(), indent=2))
    return 0


def _export(_args) -> int:
    path = config_dir() / "chatgpt.openapi.yaml"
    if not path.exists():
        raise BridgeError(f"missing {path}")
    print(path)
    print("Import this OpenAPI file into a ChatGPT action pointed at http://127.0.0.1:8765")
    print("ChatGPT cannot reach localhost by itself. Use a tunnel you control, and keep the bearer token scoped.")
    print("Do not paste a mailbox password into ChatGPT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
