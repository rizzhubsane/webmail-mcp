from assistant_bridge.checker import check_draft
from assistant_bridge.summarizer import summarize_text


def test_summary_extracts_deadline_but_not_by_video():
    summary = summarize_text(
        "Course update",
        "Office hours remain by video this week. The report is due by 28 March 2026.",
    )
    blob = " ".join(summary["deadlines"]).lower()
    assert "video" not in blob
    assert "28 march 2026" in blob
    assert summary["headline"] == "Course update"
    assert summary["bullets"]
    assert summary["suggested_action"]


def test_summary_fields_on_question():
    summary = summarize_text(
        "Internship interview slots",
        "Could you reply with your preferred slot by 30 March 2026?",
    )
    assert summary["asks"]
    assert any("30 march 2026" in item.lower() for item in summary["deadlines"])
    assert "Reply with the requested information" in summary["suggested_action"]


def test_checker_blocks_short_and_placeholders():
    short = check_draft("Thanks!")
    assert short["ok"] is False
    assert "too short" in short["blocking"]

    placeholders = check_draft("Hello, this draft is long enough to pass the length rule. [Name] [Your name]")
    assert placeholders["ok"] is False
    assert any("placeholder" in item for item in placeholders["blocking"])


def test_checker_warnings():
    text = "gonna send it lol. I guarantee this. Email me at a@b.co or 9876543210. [Name]"
    result = check_draft(text, "Could you confirm the poster layout?")
    assert result["ok"] is False
    warnings = " ".join(result["warnings"]).lower()
    assert "slang" in warnings
    assert "promise" in warnings
    assert "email" in warnings
    assert "phone" in warnings
    assert "unanswered" in warnings
    assert "poster" in warnings
