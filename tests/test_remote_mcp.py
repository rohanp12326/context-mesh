"""Tests for remote official Model Context Protocol (MCP) clients and connector dispatch."""

import pytest
from unittest.mock import AsyncMock, patch

from mcp_servers.remote_client import RemoteMCPClient, OFFICIAL_MCP_ENDPOINTS
from security.connection_testers import verify_mcp_connection
from connectors.gmail.connector import GmailConnector
from connectors.jira.connector import JiraConnector
from connectors.slack.connector import SlackConnector
from connectors.base import PermissionScope


def test_remote_mcp_client_init_and_headers():
    """Verify RemoteMCPClient initializes with official endpoints, protocol version, and auth headers."""
    # Default endpoints
    client_gmail = RemoteMCPClient("gmail", auth_token="ya29.fake-token")
    assert client_gmail.endpoint_url == OFFICIAL_MCP_ENDPOINTS["gmail"]
    assert client_gmail.headers["Authorization"] == "Bearer ya29.fake-token"
    assert client_gmail.headers["MCP-Protocol-Version"] == "2024-11-05"

    # Jira with OAuth Bearer Token
    client_jira_oauth = RemoteMCPClient("jira", auth_token="Bearer atl-oauth-token")
    assert client_jira_oauth.endpoint_url == OFFICIAL_MCP_ENDPOINTS["jira"]
    assert client_jira_oauth.headers["Authorization"] == "Bearer atl-oauth-token"

    # Jira with API Token and user_email (HTTP Basic Auth per Atlassian MCP specification)
    import base64
    client_jira_basic = RemoteMCPClient("jira", auth_token="ATATT3xFf...", user_email="dev@enterprise.com")
    expected_basic = "Basic " + base64.b64encode(b"dev@enterprise.com:ATATT3xFf...").decode("utf-8")
    assert client_jira_basic.headers["Authorization"] == expected_basic

    client_slack = RemoteMCPClient("slack", auth_token="xoxb-slack-token")
    assert client_slack.endpoint_url == OFFICIAL_MCP_ENDPOINTS["slack"]
    assert client_slack.headers["Authorization"] == "Bearer xoxb-slack-token"

    # Custom endpoint
    client_custom = RemoteMCPClient("jira", endpoint_url="https://custom.mcp/v1")
    assert client_custom.endpoint_url == "https://custom.mcp/v1"
    assert "Authorization" not in client_custom.headers


@pytest.mark.asyncio
async def test_verify_mcp_connection_validation():
    """Verify input validation and connection testing for remote MCP servers."""
    # Missing endpoint
    ok, msg, _ = await verify_mcp_connection("jira", "", "token")
    assert not ok
    assert "Endpoint URL is required" in msg

    # Missing token
    ok, msg, _ = await verify_mcp_connection("jira", "https://mcp.atlassian.com/v2/mcp", "")
    assert not ok
    assert "Authentication token is required" in msg

    # Successful mock connection
    with patch.object(RemoteMCPClient, "list_tools", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [{"name": "jira_search_issues"}, {"name": "jira_create_issue"}]
        ok, msg, latency = await verify_mcp_connection("jira", "https://mcp.atlassian.com/v2/mcp", "valid_token")
        assert ok
        assert "Connected to Jira Official MCP Server" in msg
        assert "2 tools discovered" in msg


@pytest.mark.asyncio
async def test_gmail_connector_remote_mcp_search():
    """Verify Gmail connector searches via remote MCP and normalizes results."""
    mock_threads = [
        {
            "id": "thread-abc-123",
            "subject": "Q3 Roadmap Delivery Update",
            "snippet": "We have finalized the delivery timeline for Project Atlas.",
            "from": "pm@enterprise.com",
            "date": "2026-09-08T10:00:00Z"
        }
    ]

    connector = GmailConnector(
        mode="remote_mcp",
        mcp_token="ya29.live-token-test",
        mcp_endpoint="https://gmailmcp.googleapis.com/mcp/v1"
    )

    with patch.object(RemoteMCPClient, "call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_threads
        results = await connector.search("roadmap", limit=5)

        assert len(results) == 1
        item = results[0]
        assert item.source == "gmail"
        assert item.id == "gmail-thread-abc-123"
        assert "Roadmap Delivery Update" in item.title
        assert "Project Atlas" in item.content
        assert item.author == "pm@enterprise.com"
        assert item.metadata.get("mcp") is True


@pytest.mark.asyncio
async def test_gmail_connector_remote_mcp_get_by_id():
    """Verify Gmail connector get_by_id via remote MCP."""
    mock_thread = {
        "id": "thread-xyz",
        "subject": "Security Audit Findings",
        "snippet": "All encryption keys verified.",
        "from": "sec@enterprise.com",
        "date": "2026-09-07T12:00:00Z"
    }

    connector = GmailConnector(
        mode="remote_mcp",
        mcp_token="ya29.live-token-test"
    )

    with patch.object(RemoteMCPClient, "call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_thread
        item = await connector.get_by_id("thread-xyz")

        assert item is not None
        assert item.id == "gmail-thread-xyz"
        assert "Security Audit" in item.title
        assert item.metadata.get("mcp") is True


@pytest.mark.asyncio
async def test_jira_connector_remote_mcp_search_and_mutate():
    """Verify Jira connector searches and mutates issues via remote MCP."""
    mock_issues = {
        "issues": [
            {
                "key": "ATL-999",
                "summary": "Implement Remote MCP Client",
                "status": "In Progress",
                "priority": "High",
                "assignee": "alex@enterprise.com",
                "description": "Connect ContextMesh to mcp.atlassian.com",
                "reporter": "lead@enterprise.com",
                "created": "2026-09-09T08:00:00Z"
            }
        ]
    }

    connector = JiraConnector(
        mode="remote_mcp",
        mcp_token="valid-mcp-token-123",
        mcp_endpoint="https://mcp.atlassian.com/v2/mcp"
    )

    with patch.object(RemoteMCPClient, "call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_issues
        results = await connector.search("Remote MCP", limit=5)

        assert len(results) == 1
        item = results[0]
        assert item.source == "jira"
        assert item.id == "ATL-999"
        assert "[ATL-999] Implement Remote MCP Client" in item.title
        assert "In Progress" in item.content
        assert item.author == "lead@enterprise.com"
        assert item.metadata.get("mcp") is True

        # Test mutation via MCP
        mock_call.return_value = {"status": "created", "key": "ATL-1000"}
        scope = PermissionScope(allowed_scopes=["write:jira"], can_mutate=True)
        res = await connector.mutate("create_issue", {"project": "ATL", "summary": "New MCP Task"}, scope=scope)
        assert res.get("status") == "created" or res.get("key") == "ATL-1000"


@pytest.mark.asyncio
async def test_slack_connector_remote_mcp_search():
    """Verify Slack connector searches messages via remote hosted MCP."""
    mock_messages = {
        "messages": [
            {
                "id": "slack-msg-789",
                "text": "Architecture Decision Record: MCP for Slack adopted.",
                "author": "sarah",
                "created_at": "2026-09-09T14:00:00Z",
                "channel": "proj-atlas-release",
                "url": "https://slack.com/archives/C123/p789"
            }
        ]
    }

    connector = SlackConnector(
        mode="remote_mcp",
        mcp_token="xoxb-valid-slack-token-456",
        mcp_endpoint="https://mcp.slack.com/mcp"
    )

    with patch.object(RemoteMCPClient, "call_tool", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_messages
        results = await connector.search("Architecture", limit=5)

        assert len(results) == 1
        item = results[0]
        assert item.source == "slack"
        assert item.id == "slack-msg-789"
        assert "Architecture Decision Record" in item.content
        assert item.metadata.get("mcp") is True


@pytest.mark.asyncio
async def test_remote_mcp_error_fallback_to_empty():
    """Verify connectors gracefully return empty list if remote MCP endpoints fail and no fallback is configured."""
    connector = JiraConnector(
        mode="remote_mcp",
        mcp_token="broken-token"
    )

    # When remote MCP raises network / connection error
    with patch.object(RemoteMCPClient, "call_tool", side_effect=Exception("Connection timed out")):
        # Should return empty list rather than fake synthetic data
        results = await connector.search("blocker", limit=5)
        assert isinstance(results, list)
        assert len(results) == 0


# =========================================================================
# Composio MCP Client & Streamlined Authentication Tests
# =========================================================================

def test_composio_client_init_and_properties():
    from mcp_servers.composio_client import ComposioMCPClient
    client = ComposioMCPClient(api_key="comp_live_test_key_123", user_id="custom_user")
    assert client.api_key == "comp_live_test_key_123"
    assert client.user_id == "custom_user"
    assert client.headers["x-api-key"] == "comp_live_test_key_123"
    assert client.is_configured() is True

    empty_client = ComposioMCPClient(api_key="")
    assert "x-api-key" not in empty_client.headers
    assert empty_client.is_configured() is False


@pytest.mark.asyncio
async def test_composio_get_auth_url():
    from mcp_servers.composio_client import ComposioMCPClient
    import httpx

    client = ComposioMCPClient(api_key="comp_live_key_999", user_id="user_abc")

    class MockResponse:
        status_code = 200
        def json(self):
            return {"redirect_url": "https://connect.composio.dev/auth/start?session=xyz"}

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json=None, **kwargs):
            return MockResponse()

    with patch.object(httpx, "AsyncClient", MockAsyncClient):
        url = await client.get_auth_url("jira")
        assert url == "https://connect.composio.dev/auth/start?session=xyz"


@pytest.mark.asyncio
async def test_composio_check_connection_status():
    from mcp_servers.composio_client import ComposioMCPClient
    import httpx

    client = ComposioMCPClient(api_key="comp_live_key_999", user_id="user_abc")

    class MockResponse:
        status_code = 200
        def json(self):
            return {
                "items": [
                    {"id": "ca_jira_123", "status": "ACTIVE", "toolkit": {"slug": "jira"}}
                ]
            }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, params=None, **kwargs):
            return MockResponse()

    with patch.object(httpx, "AsyncClient", MockAsyncClient):
        is_active, acc_id = await client.check_connection_status("jira")
        assert is_active is True
        assert acc_id == "ca_jira_123"


@pytest.mark.asyncio
async def test_composio_call_tool_action_mapping():
    from mcp_servers.composio_client import ComposioMCPClient
    import httpx

    client = ComposioMCPClient(api_key="comp_live_key_999", user_id="user_abc")

    class MockResponse:
        status_code = 200
        def json(self):
            return {
                "successful": True,
                "data": {"issues": [{"key": "ATL-101", "summary": "Fix auth blocker"}]}
            }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json=None, **kwargs):
            return MockResponse()

    with patch.object(httpx, "AsyncClient", MockAsyncClient):
        with patch.object(client, "get_or_create_mcp_session", new_callable=AsyncMock) as mock_sess:
            mock_sess.return_value = {"mcp": {"url": ""}}
            res = await client.call_tool("jira.search_issues", {"query": "project = ATL"})
            assert "issues" in res
            assert res["issues"][0]["key"] == "ATL-101"


@pytest.mark.asyncio
async def test_verify_composio_connection_and_app():
    from security.connection_testers import verify_composio_connection, verify_composio_app_connection
    from mcp_servers.composio_client import ComposioMCPClient
    import httpx

    # Empty key
    ok, msg, _ = await verify_composio_connection("")
    assert ok is False
    assert "empty" in msg.lower()

    # Successful connection
    class MockResponse:
        status_code = 200
        def json(self):
            return {"items": [{"id": "ca_1"}]}

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            return MockResponse()

    with patch.object(httpx, "AsyncClient", MockAsyncClient):
        ok, msg, _ = await verify_composio_connection("valid_key")
        assert ok is True
        assert "Successfully connected to Composio" in msg

    # App connection test
    with patch.object(ComposioMCPClient, "check_connection_status", new_callable=AsyncMock) as mock_chk:
        mock_chk.return_value = (True, "ca_slack_123")
        ok, msg, _ = await verify_composio_app_connection("slack")
        assert ok is True
        assert "actively connected" in msg.lower()

