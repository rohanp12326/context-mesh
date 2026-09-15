"""Unit tests for Jira, Slack, and Gmail connectors."""

import pytest
from connectors.base import PermissionScope


@pytest.mark.asyncio
async def test_jira_unconfigured_returns_empty(jira_connector, monkeypatch):
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT
    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda svc: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)
    items = await jira_connector.search("blocker", limit=5)
    assert items == []


@pytest.mark.asyncio
async def test_jira_unconfigured_get_by_id(jira_connector, monkeypatch):
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT
    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda svc: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)
    item = await jira_connector.get_by_id("ATL-101")
    assert item is None


@pytest.mark.asyncio
async def test_slack_unconfigured_returns_empty(slack_connector, monkeypatch):
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT
    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda svc: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)
    items = await slack_connector.search("Atlas launch plan", limit=5)
    assert items == []


@pytest.mark.asyncio
async def test_gmail_unconfigured_returns_empty(gmail_connector, monkeypatch):
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT
    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda svc: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)
    items = await gmail_connector.search("commitments", limit=5)
    assert items == []


@pytest.mark.asyncio
async def test_jira_live_rest_search(monkeypatch):
    import httpx
    from connectors.jira.connector import JiraConnector
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT

    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)

    conn = JiraConnector(
        mode="live",
        base_url="https://company.atlassian.net",
        user_email="user@company.com",
        api_token="token123"
    )

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *args, **kwargs):
            class MockResp:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {
                        "issues": [
                            {
                                "key": "PROJ-101",
                                "fields": {
                                    "summary": "Fix authentication session timeout",
                                    "description": "Session tokens expire too early under high load.",
                                    "status": {"name": "In Progress"},
                                    "priority": {"name": "High"},
                                    "assignee": {"displayName": "Alex Developer", "emailAddress": "alex@company.com"},
                                    "updated": "2026-09-10T10:00:00.000Z"
                                }
                            }
                        ]
                    }
            return MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    items = await conn.search("authentication", limit=1)
    assert len(items) == 1
    assert items[0].id == "PROJ-101"
    assert "Fix authentication" in items[0].title


@pytest.mark.asyncio
async def test_connector_permissions(jira_connector):
    scope = PermissionScope(allowed_scopes=["read:slack"])  # Missing read:jira
    with pytest.raises(PermissionError):
        await jira_connector.search("Atlas", scope=scope)


@pytest.mark.asyncio
async def test_gmail_live_rest_search(monkeypatch):
    import httpx
    from connectors.gmail.connector import GmailConnector
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT

    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)

    conn = GmailConnector(mode="live", access_token="ya29.test_token")

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
                    if "messages?" in url or url.endswith("/messages"):
                        return {"messages": [{"id": "msg_live_01"}]}
                    else:
                        return {
                            "id": "msg_live_01",
                            "snippet": "Priya confirmed the launch date is delayed.",
                            "payload": {
                                "headers": [
                                    {"name": "Subject", "value": "Release Update"},
                                    {"name": "From", "value": "priya@company.com"},
                                    {"name": "Date", "value": "2026-09-09"}
                                ]
                            }
                        }
            return MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    items = await conn.search("release", limit=1)
    assert len(items) == 1
    assert items[0].id == "gmail-msg_live_01"
    assert "Release Update" in items[0].title
    assert "delayed" in items[0].content


@pytest.mark.asyncio
async def test_slack_live_search(monkeypatch):
    import httpx
    from connectors.slack.connector import SlackConnector
    from mcp_servers.composio_client import COMPOSIO_CLIENT
    from security.vault import VAULT

    monkeypatch.setattr(COMPOSIO_CLIENT, "is_configured", lambda: False)
    monkeypatch.setattr(VAULT, "is_composio_connected", lambda svc: False)

    conn = SlackConnector(mode="live", bot_token="xoxb-test-token")

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
                                    "iid": "msg-999",
                                    "text": "Detailed architecture guidelines for Atlas in Slack.",
                                    "username": "sarah",
                                    "ts": "1710000000.000",
                                    "channel": {"id": "C123", "name": "proj-atlas-release"},
                                    "permalink": "https://slack.com/archives/C123/p1710000000"
                                }
                            ]
                        }
                    }
            return MockResp()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    items = await conn.search("architecture", limit=1)
    assert len(items) == 1
    assert items[0].id == "slack-msg-1710000000.000"
    assert "Detailed architecture guidelines" in items[0].content

