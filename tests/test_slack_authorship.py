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
async def test_slack_live_search_with_author(monkeypatch):
    """Verify Slack live search extracts and formats author appropriately."""
    import httpx
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT

    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)

    conn = SlackConnector(mode="live", bot_token="xoxb-fake")

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, *args, **kwargs):
            class MockResp:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {
                        "ok": True,
                        "messages": {
                            "matches": [
                                {
                                    "iid": "msg-123",
                                    "text": "Hello team, here is the spec",
                                    "username": "sarah.jenkins",
                                    "ts": "1710000000.000",
                                    "channel": {"id": "C1", "name": "general"}
                                }
                            ]
                        }
                    }
            return MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    items = await conn.search("sarah", limit=5)
    assert len(items) == 1
    assert "sarah" in (items[0].author or "").lower()


def test_synthesis_directionality_prompt():
    """Verify synthesis system prompt contains explicit directionality requirements."""
    assert "Verify message authorship and directionality" in SYNTHESIS_SYSTEM_PROMPT
    assert "Do NOT attribute messages addressed TO Person X" in SYNTHESIS_SYSTEM_PROMPT
    assert "Distinguish incoming communications" in SYNTHESIS_SYSTEM_PROMPT


def test_offline_synthesis_includes_author():
    """Verify fallback synthesis includes author name for Slack evidence."""
    synthesizer = AnswerSynthesizer()
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
    res = synthesizer._format_evidence_fallback("does Monali have any message for me", evidence, [])
    assert "Rohan Patil" in res
