from assistant_bridge.service import Bridge


def test_first_sync_is_quiet_and_later_sync_reports_new_mail(bridge, home):
    first = bridge.sync_inbox()
    assert first["first_sync"] is True
    assert first["new_count"] == 0
    assert first["new"] == []

    seen = set(bridge.state.read()["seen_ids"])
    assert seen == {item["id"] for item in bridge.list_inbox()}

    again = bridge.sync_inbox()
    assert again["new_count"] == 0

    bridge.add_mock_message(
        {
            "id": "demo-new",
            "from": "tutor@example.edu",
            "to": ["student@example.edu"],
            "subject": "New lab slot",
            "date": "2026-04-02T10:00:00+00:00",
            "body": "Could you confirm the new lab slot by 4 April 2026?",
        }
    )
    third = bridge.sync_inbox()
    assert third["first_sync"] is False
    assert third["new_count"] == 1
    assert third["new"][0]["id"] == "demo-new"
    assert third["new"][0]["summary"]["headline"] == "New lab slot"
    assert third["new"][0]["summary"]["deadlines"]

    briefing = bridge.get_briefing()
    assert briefing["new_count"] == 1

    reopened = Bridge.open(home)
    assert reopened.get_briefing()["new"][0]["id"] == "demo-new"
    assert "demo-new" in reopened.state.read()["seen_ids"]
