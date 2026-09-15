"""Model Context Protocol (MCP) Standard Server for ContextMesh.

Exposes enterprise tools (Jira, Slack, Gmail), public web search, and the
high-level ContextMesh agent as a native Model Context Protocol server.
External MCP clients (Claude Desktop, Cursor, Continue, Windsurf, VS Code)
can connect over stdio or SSE.

Usage:
    # Run over stdio (standard for Claude Desktop / Cursor):
    python -m mcp_servers.server

    # Or run via CLI with explicit transport:
    python -m mcp_servers.server --transport stdio
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer
from mcp_servers.registry import MCPToolRegistry
from mcp_servers.base import MCPToolCall
from connectors.base import PermissionScope
from agent.graph import ContextMeshAgent

server = MCPServer(
    name="context-mesh",
    version="0.1.0",
    description="ContextMesh: Enterprise AI intelligence and cross-tool retrieval server"
)

# Shared registry and agent instances
registry = MCPToolRegistry()
agent = ContextMeshAgent(tool_registry=registry)


@server.tool(
    name="jira_search_issues",
    description="Search Jira issues using JQL syntax or keywords (e.g. project = ATL AND status != Done)."
)
async def jira_search_issues(query: str, limit: int = 10) -> str:
    """Search Jira issues."""
    call = MCPToolCall(tool_name="jira.search_issues", arguments={"query": query, "limit": limit})
    res = await registry.execute_tool(call)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="jira_get_issue",
    description="Fetch full details for a specific Jira issue by key (e.g. ATL-101)."
)
async def jira_get_issue(issue_key: str) -> str:
    """Get specific Jira issue."""
    call = MCPToolCall(tool_name="jira.get_issue", arguments={"issue_key": issue_key})
    res = await registry.execute_tool(call)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="jira_create_issue",
    description="Create a new Jira issue/task. MUTATION OPERATION: requires explicit human approval (approved=True)."
)
async def jira_create_issue(project: str, summary: str, description: str = "", priority: str = "Medium", approved: bool = False) -> str:
    """Create Jira issue (mutation operation)."""
    call = MCPToolCall(
        tool_name="jira.create_issue",
        arguments={"project": project, "summary": summary, "description": description, "priority": priority}
    )
    scope = PermissionScope(user_id="mcp_client", can_mutate=approved, allowed_scopes=["write:jira"])
    res = await registry.execute_tool(call, scope=scope)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="slack_search_messages",
    description="Search Slack messages, channels, discussions, and canvases."
)
async def slack_search_messages(query: str, limit: int = 10) -> str:
    """Search Slack messages."""
    call = MCPToolCall(tool_name="slack.search_messages", arguments={"query": query, "limit": limit})
    res = await registry.execute_tool(call)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="slack_get_thread",
    description="Get full content of a Slack thread or discussion by thread/channel ID."
)
async def slack_get_thread(thread_id: str) -> str:
    """Get Slack thread content."""
    call = MCPToolCall(tool_name="slack.get_thread", arguments={"thread_id": thread_id})
    res = await registry.execute_tool(call)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="slack_post_message",
    description="Post a message to a Slack channel (MUTATION OPERATION: requires explicit human approval (approved=True))."
)
async def slack_post_message(channel: str, text: str, approved: bool = False) -> str:
    """Post message to Slack channel."""
    call = MCPToolCall(tool_name="slack.post_message", arguments={"channel": channel, "text": text})
    scope = PermissionScope(user_id="mcp_client", can_mutate=approved, allowed_scopes=["write:slack"])
    res = await registry.execute_tool(call, scope=scope)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="gmail_search_messages",
    description="Search Gmail messages and threads for discussions, decisions, and commitments."
)
async def gmail_search_messages(query: str, limit: int = 10) -> str:
    """Search Gmail messages."""
    call = MCPToolCall(tool_name="gmail.search_messages", arguments={"query": query, "limit": limit})
    res = await registry.execute_tool(call)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="gmail_get_thread",
    description="Get full email thread by thread ID."
)
async def gmail_get_thread(thread_id: str) -> str:
    """Get Gmail email thread."""
    call = MCPToolCall(tool_name="gmail.get_thread", arguments={"thread_id": thread_id})
    res = await registry.execute_tool(call)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


@server.tool(
    name="contextmesh_query",
    description=(
        "Ask ContextMesh an enterprise question across Jira, Slack, Gmail, and the web. "
        "Automatically decomposes query, retrieves cross-system evidence, detects "
        "contradictions, and generates cited answer."
    )
)
async def contextmesh_query(query: str, user_id: str = "mcp_user") -> str:
    """Run full ContextMesh query orchestration."""
    resp = await agent.run(query=query, user_id=user_id)
    citations_info = [
        {"claim": c.claim, "source": c.source_url or c.evidence_id}
        for c in resp.citations
    ]
    result = {
        "answer": resp.answer,
        "confidence": resp.confidence,
        "citations": citations_info,
        "requires_approval": resp.requires_approval
    }
    return json.dumps(result, indent=2)


@server.tool(
    name="web_search",
    description="Search the public web for current events, recent news, and live information."
)
async def web_search(query: str, limit: int = 5) -> str:
    """Search the public web."""
    call = MCPToolCall(tool_name="web.search", arguments={"query": query, "limit": limit})
    res = await registry.execute_tool(call)
    if not res.success:
        return f"Error: {res.error}"
    return json.dumps(res.data, indent=2)


def main():
    parser = argparse.ArgumentParser(description="ContextMesh Model Context Protocol Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)"
    )
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv()

    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
