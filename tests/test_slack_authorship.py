"""Unit tests for Slack author extraction, query normalization, observation formatting, and message directionality."""

import pytest
from connectors.slack.connector import SlackConnector, _extract_slack_author
from connectors.base import ConnectorItem
from agent.synthesis import AnswerSynthesizer, SYNTHESIS_SYSTEM_PROMPT
from agent.state import QueryPlan
from retrieval.normalization import Evidence


def test_extract_slack_author():
    """Test extracting human-readable author names from various Slack payload shapes."""
    # 1. user_profile with real_name
    payload1 = {
        "user": "U08DU2K3812",
        "user_profile": {
            "real_name": "Rohan Patil",
            "display_name": "rohan",
            "name": "rohanp12326",
        }
    }
    assert _extract_slack_author(payload1) == "Rohan Patil"

    # 2. user_profile with display_name only
    payload2 = {
        "user": "U08DU2K3812",
        "user_profile": {
            "display_name": "Monali",
            "name": "monali_s",
        }
    }
    assert _extract_slack_author(payload2) == "Monali"

    # 3. Direct author string
    payload3 = {"author": "Sarah Jenkins", "user": "U123"}
    assert _extract_slack_author(payload3) == "Sarah Jenkins"

    # 4. user_name / sender_name
    payload4 = {"sender_name": "Alex Chen", "user": "U456"}
    assert _extract_slack_author(payload4) == "Alex Chen"

    # 5. Fallback to username
    payload5 = {"username": "priya.sharma", "user": "U789"}
    assert _extract_slack_author(payload5) == "priya.sharma"

    # 6. Fallback to user ID
    payload6 = {"user": "U999"}
    assert _extract_slack_author(payload6) == "U999"

    # 7. Empty dict
    assert _extract_slack_author({}) == "slack_user"

    # 8. User profile cache resolution
    payload_cached = {"user": "U0C0VHT43NW", "username": "srsonawanemonali"}
    cache = {"U0C0VHT43NW": {"real_name": "Monali Sonawane", "display_name": "Monali"}}
    assert _extract_slack_author(payload_cached, cache) == "Monali Sonawane"


def test_sanitize_slack_query_generic():
    """Test generic query sanitization without invalid boolean operators for Slack."""
    # Single name preserved cleanly without breaking 'OR from:'
    assert SlackConnector._sanitize_slack_query("Monali") == "Monali"
    assert SlackConnector._sanitize_slack_query("Sarah") == "Sarah"
    assert SlackConnector._sanitize_slack_query("Monali Sonawane") == "Monali Sonawane"

    # Natural language from / by
    assert SlackConnector._sanitize_slack_query("messages from Monali") == "from:Monali"
    assert SlackConnector._sanitize_slack_query("from Monali") == "from:Monali"
    assert SlackConnector._sanitize_slack_query("sent by Sarah") == "from:Sarah"

    # Conversational intent
    assert SlackConnector._sanitize_slack_query("does monali have any message for me") in ("from:monali", "monali")
    assert SlackConnector._sanitize_slack_query("is there any message from Monali") == "from:Monali"

    # Natural language to
    assert SlackConnector._sanitize_slack_query("messages to Monali") == "to:Monali"
    assert SlackConnector._sanitize_slack_query("messages to me") == "*"

    # Natural language in channel
    assert SlackConnector._sanitize_slack_query("in channel new-channel") == "in:new-channel"
    assert SlackConnector._sanitize_slack_query("in #general") == "in:general"

    # Explicit Slack operators preserved
    assert SlackConnector._sanitize_slack_query("from:@monali") == "from:@monali"
    assert SlackConnector._sanitize_slack_query("in:#releases roadmap") == "in:#releases roadmap"
    assert SlackConnector._sanitize_slack_query("to:me status") == "to:me status"

    # Meta words convert to wildcard
    assert SlackConnector._sanitize_slack_query("what are my latest slack messages") == "*"
    assert SlackConnector._sanitize_slack_query("") == "*"
    assert SlackConnector._sanitize_slack_query("   ") == "*"


@pytest.mark.asyncio
async def test_slack_mock_search_with_author():
    """Verify mock Slack search matches author when searched directly or via from:."""
    conn = SlackConnector(mode="mock")

    # Search author by name
    items_sarah = await conn.search("sarah")
    assert len(items_sarah) > 0
    assert any("sarah" in (it.author or "").lower() for it in items_sarah)

    # Search via from: filter
    items_from_priya = await conn.search("from:priya")
    assert len(items_from_priya) > 0
    assert all("priya" in (it.author or "").lower() for it in items_from_priya)

    # Search via in: channel filter
    items_channel = await conn.search("in:architecture")
    assert len(items_channel) > 0
    assert all(it.metadata.get("channel") == "architecture" for it in items_channel)


def test_synthesis_directionality_prompt():
    """Verify synthesis system prompt contains explicit directionality requirements."""
    assert "Verify message authorship and directionality" in SYNTHESIS_SYSTEM_PROMPT
    assert "Do NOT attribute messages addressed TO Person X" in SYNTHESIS_SYSTEM_PROMPT
    assert "Distinguish incoming communications" in SYNTHESIS_SYSTEM_PROMPT


def test_offline_synthesis_includes_author():
    """Verify offline synthesis includes author name for Slack evidence."""
    synthesizer = AnswerSynthesizer()
    plan = QueryPlan(user_intent="test", steps=[], risk_level="low")
    evidence = [
        Evidence(
            evidence_id="slack:101",
            source="slack",
            source_object_id="msg-101",
            title="Slack #new-channel: Hey Monali",
            content="Hey Monali, I've added sample tasks to Jira.",
            author="Rohan Patil",
            source_url="https://slack.com/test",
            content_hash="abc12345",
            raw_data={}
        )
    ]
    res = synthesizer._offline_synthesize("does Monali have any message for me", evidence, [])
    assert "Rohan Patil" in res
