from assistant_bridge.connectors.imap_conn import build_outgoing


def test_outgoing_message_includes_date_header():
    message = build_outgoing(
        to=["ce1240369@iitd.ac.in"],
        subject="test",
        body="manasvi i am testing my webmail bot",
        from_addr="ce1240041@iitd.ac.in",
    )
    raw = message.as_bytes()
    assert message["Date"]
    assert b"\nDate:" in raw or raw.startswith(b"Date:")
    assert b"Message-ID:" in raw
    assert b"Missing required header" not in raw
