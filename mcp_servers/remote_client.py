"""Client bridge for connecting to official external remote MCP servers.

Supports:
- Google Workspace Gmail MCP: https://gmailmcp.googleapis.com/mcp/v1
- Atlassian Rovo Jira MCP:    https://mcp.atlassian.com/v2/mcp
- Slack Hosted MCP:           https://mcp.slack.com/mcp
"""

import asyncio
import base64
import json
from typing import Any, Dict, List, Optional
import httpx

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp_servers.composio_client import ComposioMCPClient, COMPOSIO_CLIENT
from observability.logging import get_logger

logger = get_logger("mcp.remote_client")

OFFICIAL_MCP_ENDPOINTS = {
    "gmail": "https://gmailmcp.googleapis.com/mcp/v1",
    "jira": "https://mcp.atlassian.com/v2/mcp",
    "slack": "https://mcp.slack.com/mcp",
    "composio": "https://backend.composio.dev/api/v3.1",
}


class RemoteMCPClient:
    """Client for connecting to remote Model Context Protocol endpoints (Composio MCP or official endpoints)."""

    def __init__(
        self,
        service: str,
        endpoint_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        user_email: Optional[str] = None,
        timeout: float = 10.0,
        composio_client: Optional[ComposioMCPClient] = None,
    ):
        self.service = service.lower()
        self.endpoint_url = endpoint_url or OFFICIAL_MCP_ENDPOINTS.get(self.service, "")
        self.auth_token = auth_token
        self.user_email = user_email
        self.timeout = timeout
        self.composio = composio_client or COMPOSIO_CLIENT

    @property
    def headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "text/event-stream, application/json",
            "User-Agent": "ContextMesh-MCP-Client/1.0",
            "MCP-Protocol-Version": "2024-11-05",
        }
        if self.auth_token:
            token = self.auth_token.strip()
            if token.startswith("Bearer ") or token.startswith("Basic "):
                headers["Authorization"] = token
            elif self.service == "jira" and self.user_email:
                # Atlassian MCP Basic Auth for Personal API Tokens
                raw_cred = f"{self.user_email.strip()}:{token}"
                encoded = base64.b64encode(raw_cred.encode("utf-8")).decode("utf-8")
                headers["Authorization"] = f"Basic {encoded}"
            else:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def should_use_composio(self) -> bool:
        """Check whether to route calls to Composio MCP rather than direct provider endpoints."""
        if self.service == "composio" or "composio" in self.endpoint_url:
            return True
        if not self.composio.is_configured():
            return False
        try:
            from security.vault import VAULT
            if VAULT.is_composio_connected(self.service):
                return True
        except Exception:
            pass
        if not self.auth_token:
            return True
        return False

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Query remote MCP server for exposed tools."""
        if self.should_use_composio():
            session = await self.composio.get_or_create_mcp_session()
            mcp_url = session.get("mcp", {}).get("url")
            if mcp_url:
                try:
                    async with sse_client(mcp_url, headers=self.composio.headers, timeout=self.timeout) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as session_obj:
                            await session_obj.initialize()
                            tools_resp = await session_obj.list_tools()
                            return [t.model_dump() if hasattr(t, "model_dump") else t.__dict__ for t in tools_resp]
                except Exception as e:
                    logger.debug(f"Composio MCP list_tools SSE failed: {e}")
            return [
                {"name": "jira_search_issues", "description": "Search Jira issues via Composio"},
                {"name": "jira_create_issue", "description": "Create Jira issue via Composio"},
                {"name": "slack_search_messages", "description": "Search Slack messages via Composio"},
                {"name": "gmail_search_messages", "description": "Search Gmail messages via Composio"},
            ]

        if not self.endpoint_url:
            raise ValueError(f"No endpoint URL configured for service '{self.service}'")

        try:
            # Try SSE transport first
            async with sse_client(self.endpoint_url, headers=self.headers, timeout=self.timeout) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools_resp = await session.list_tools()
                    return [t.model_dump() if hasattr(t, "model_dump") else t.__dict__ for t in tools_resp]
        except Exception as e:
            logger.debug(f"SSE transport failed for {self.endpoint_url}: {e}. Retrying with Streamable HTTP.")
            try:
                async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as http_client:
                    async with streamable_http_client(self.endpoint_url, http_client=http_client) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            tools_resp = await session.list_tools()
                            return [t.model_dump() if hasattr(t, "model_dump") else t.__dict__ for t in tools_resp]
            except Exception as e2:
                logger.warning(f"Remote MCP list_tools failed on {self.endpoint_url}: {e2}")
                raise

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a tool on the remote official MCP server or Composio MCP."""
        if self.should_use_composio():
            return await self.composio.call_tool(tool_name, arguments)

        if not self.endpoint_url:
            raise ValueError(f"No endpoint URL configured for service '{self.service}'")

        logger.info(f"Calling remote MCP tool '{tool_name}' on {self.endpoint_url}")

        try:
            # Try SSE transport first
            async with sse_client(self.endpoint_url, headers=self.headers, timeout=self.timeout) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return self._extract_result(result)
        except Exception as e:
            logger.debug(f"SSE call failed: {e}. Retrying via Streamable HTTP transport.")
            try:
                async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as http_client:
                    async with streamable_http_client(self.endpoint_url, http_client=http_client) as (read_stream, write_stream):
                        async with ClientSession(read_stream, write_stream) as session:
                            await session.initialize()
                            result = await session.call_tool(tool_name, arguments)
                            return self._extract_result(result)
            except Exception as e2:
                logger.warning(f"Remote MCP execution failed for {tool_name} on {self.endpoint_url}: {e2}")
                if self.composio.is_configured():
                    logger.info(f"Falling back to Composio tool execution for {tool_name}")
                    return await self.composio.call_tool(tool_name, arguments)
                raise

    def _extract_result(self, result: Any) -> Any:
        """Extract content from MCP CallToolResult."""
        if hasattr(result, "content") and result.content:
            text_blocks = []
            for item in result.content:
                if hasattr(item, "text"):
                    text_blocks.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    text_blocks.append(item["text"])
            if text_blocks:
                combined = "\n".join(text_blocks)
                try:
                    return json.loads(combined)
                except Exception:
                    return combined
        return result
