"""Tests for AppRouter, Zero-Tool bypass, and MCP Server integration."""

import pytest
import json
from agent.graph import ContextMeshAgent
from agent.router import AppRouter
from mcp_servers.server import server, jira_search_issues, slack_search_messages, gmail_search_messages, contextmesh_query


def test_app_router_zero_tool_detection():
    assert AppRouter.is_zero_tool_query("who is the president of america") is True
    assert AppRouter.is_zero_tool_query("how to setup windows 11") is True
    assert AppRouter.is_zero_tool_query("hello there") is True
    assert AppRouter.is_zero_tool_query("what are my recent mails") is False
    assert AppRouter.is_zero_tool_query("what are my opened tasks") is False


def test_app_router_app_determination():
    # Email -> Gmail only
    assert AppRouter.determine_apps("what are my recent mails") == ["gmail"]
    assert AppRouter.determine_apps("search my email inbox for updates") == ["gmail"]

    # Opened tasks -> Jira + Slack
    assert AppRouter.determine_apps("what are my opened tasks") == ["jira", "slack"]
    assert AppRouter.determine_apps("show active action items and tasks") == ["jira", "slack"]

    # Closed tasks / tickets -> Jira only
    assert AppRouter.determine_apps("what are my closed tasks") == ["jira"]
    assert AppRouter.determine_apps("show done tasks and closed issues") == ["jira"]
    assert AppRouter.determine_apps("what are my resolved tasks") == ["jira"]

    # Jira tickets -> Jira only
    assert AppRouter.determine_apps("show my tickets in jira") == ["jira"]
    assert AppRouter.determine_apps("list open bugs and blockers") == ["jira"]

    # Slack messages/channels -> Slack only
    assert AppRouter.determine_apps("search the slack runbook") == ["slack"]

    # Zero tool queries -> empty list
    assert AppRouter.determine_apps("who is the president of america") == []
    assert AppRouter.determine_apps("how to setup windows 11") == []


@pytest.mark.asyncio
async def test_agent_zero_tool_query_president():
    agent = ContextMeshAgent()
    res = await agent.run("who is the president of america", thread_id="test_zero_1", allow_auth_gate=True)

    assert res is not None
    assert res.plan is not None
    assert res.plan.user_intent == "direct_answer"
    assert len(res.plan.steps) == 0
    assert len(res.citations) == 0
    assert res.auth_required is False
    assert "President of the United States" in res.answer or "Biden" in res.answer
    assert "No relevant evidence was found across Jira" not in res.answer


@pytest.mark.asyncio
async def test_agent_zero_tool_query_windows():
    agent = ContextMeshAgent()
    res = await agent.run("how to setup windows 11", thread_id="test_zero_2", allow_auth_gate=True)

    assert res is not None
    assert res.plan is not None
    assert res.plan.user_intent == "direct_answer"
    assert len(res.plan.steps) == 0
    assert len(res.citations) == 0
    assert res.auth_required is False
    assert "Windows 11" in res.answer
    assert "No relevant evidence was found across Jira" not in res.answer


@pytest.mark.asyncio
async def test_agent_recent_mails_gmail_only():
    agent = ContextMeshAgent()
    res = await agent.run("what are my recent mails", thread_id="test_mails")

    assert res is not None
    assert res.plan is not None
    tools_used = [s.tool for s in res.plan.steps]
    assert tools_used == ["gmail.search_messages"]
    assert "jira.search_issues" not in tools_used
    assert "slack.search_messages" not in tools_used
    assert len(res.citations) > 0
    assert any(c.source_type == "gmail" for c in res.citations)


@pytest.mark.asyncio
async def test_agent_opened_tasks_jira_and_slack():
    agent = ContextMeshAgent()
    res = await agent.run("what are my opened tasks", thread_id="test_tasks")

    assert res is not None
    assert res.plan is not None
    tools_used = [s.tool for s in res.plan.steps]
    assert "jira.search_issues" in tools_used or "slack.search_messages" in tools_used
    assert "gmail.search_messages" not in tools_used
    assert len(res.citations) > 0


def test_gmail_query_normalization():
    from mcp_servers.composio_client import normalize_gmail_query

    assert normalize_gmail_query("latest recent recent") == "in:inbox newer_than:7d"
    assert normalize_gmail_query("") == "in:inbox newer_than:7d"
    assert normalize_gmail_query("*") == "in:inbox newer_than:7d"
    assert normalize_gmail_query("my recent emails") == "in:inbox newer_than:7d"
    # Explicit Gmail operators are preserved
    assert normalize_gmail_query("from:me") == "from:me"
    assert normalize_gmail_query("in:sent") == "in:sent"
    assert normalize_gmail_query("project atlas invoice") == "project atlas invoice"


def test_app_router_web_detection():
    assert AppRouter.is_web_query("what is the latest news") is True
    assert AppRouter.is_web_query("latest AI news 2026") is True
    assert AppRouter.is_web_query("what are my recent mails") is False
    assert AppRouter.is_zero_tool_query("what is the latest news") is False
    assert AppRouter.determine_apps("latest AI news") == ["web"]


def test_registry_exposes_web_search():
    from mcp_servers.registry import MCPToolRegistry

    reg = MCPToolRegistry()
    names = [t.name for t in reg.get_all_tool_definitions()]
    assert "web.search" in names


@pytest.mark.asyncio
async def test_mcp_server_tool_listing_and_execution():
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]

    expected = [
        "jira_search_issues",
        "jira_get_issue",
        "jira_create_issue",
        "slack_search_messages",
        "slack_get_thread",
        "slack_post_message",
        "gmail_search_messages",
        "gmail_get_thread",
        "web_search",
        "contextmesh_query"
    ]
    for exp in expected:
        assert exp in tool_names

    # Test direct tool execution
    jira_res = await jira_search_issues(query="project = ATL", limit=2)
    jira_data = json.loads(jira_res)
    assert isinstance(jira_data, list)

    slack_res = await slack_search_messages(query="Atlas", limit=2)
    slack_data = json.loads(slack_res)
    assert isinstance(slack_data, list)

    gmail_res = await gmail_search_messages(query="Atlas", limit=2)
    gmail_data = json.loads(gmail_res)
    assert isinstance(gmail_data, list)

    cm_res = await contextmesh_query(query="what are my recent mails")
    cm_data = json.loads(cm_res)
    assert "answer" in cm_data
    assert "citations" in cm_data
