import hashlib
import json

from assistant_bridge.tokens import TokenStore, hash_token


def test_token_is_hashed_and_revocable(home):
    store = TokenStore(home)
    raw, record = store.create("cursor", ["read", "draft"])

    assert raw.startswith("ab_")
    on_disk = (home / "tokens.json").read_text(encoding="utf-8")
    assert raw not in on_disk
    assert hash_token(raw) == hashlib.sha256(raw.encode()).hexdigest()
    assert hash_token(raw) in on_disk

    verified = store.verify(raw)
    assert verified is not None
    assert verified["id"] == record["id"]
    assert verified["scopes"] == ["read", "draft"]
    assert "hash" not in verified

    listed = store.public_list()
    assert "hash" not in listed[0]
    assert raw not in json.dumps(listed)

    store.revoke(record["id"])
    assert store.verify(raw) is None
    assert store.public_list()[0]["revoked"] is True
