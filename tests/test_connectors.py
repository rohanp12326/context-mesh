"""Unit tests for Jira, Notion, and Gmail connectors."""

import pytest
from connectors.base import PermissionScope


@pytest.mark.asyncio
async def test_jira_mock_search(jira_connector):
    # Test searching for blockers
    items = await jira_connector.search("blocker", limit=5)
    assert len(items) > 0
    assert any("ATL-101" in item.id for item in items)


@pytest.mark.asyncio
async def test_jira_get_by_id(jira_connector):
    item = await jira_connector.get_by_id("ATL-101")
    assert item is not None
    assert item.id == "ATL-101"
    assert "token refresh" in item.title.lower()


@pytest.mark.asyncio
async def test_notion_mock_search(notion_connector):
    items = await notion_connector.search("Atlas launch plan", limit=5)
    assert len(items) > 0
    assert any("atlas-spec" in item.id for item in items)


@pytest.mark.asyncio
async def test_gmail_mock_search(gmail_connector):
    items = await gmail_connector.search("commitments", limit=5)
    assert len(items) > 0
    assert any("priya" in item.author.lower() for item in items)


@pytest.mark.asyncio
async def test_connector_permissions(jira_connector):
    scope = PermissionScope(allowed_scopes=["read:notion"])  # Missing read:jira
    with pytest.raises(PermissionError):
        await jira_connector.search("Atlas", scope=scope)
