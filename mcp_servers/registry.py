"""Central MCP tool registry managing tool discovery and parallel tool dispatch."""

import asyncio
from typing import Any, Dict, List, Optional
from connectors.base import PermissionScope
from mcp_servers.base import MCPToolCall, MCPToolDefinition, MCPToolResult
from mcp_servers.jira_server import JiraMCPServer
from mcp_servers.slack_server import SlackMCPServer
from mcp_servers.gmail_server import GmailMCPServer
from mcp_servers.web_server import WebMCPServer


class MCPToolRegistry:
    """Registry coordinating all MCP tool servers."""

    def __init__(
        self,
        jira_server: Optional[JiraMCPServer] = None,
        slack_server: Optional[SlackMCPServer] = None,
        gmail_server: Optional[GmailMCPServer] = None,
        web_server: Optional[WebMCPServer] = None,
    ):
        self.jira = jira_server or JiraMCPServer()
        self.slack = slack_server or SlackMCPServer()
        self.gmail = gmail_server or GmailMCPServer()
        self.web = web_server or WebMCPServer()

        self._tools: Dict[str, MCPToolDefinition] = {}
        self._handlers: Dict[str, Any] = {}
        self._register_all()

    def _register_all(self):
        for tool_def in self.jira.get_tool_definitions():
            self._tools[tool_def.name] = tool_def
            self._handlers[tool_def.name] = self.jira

        for tool_def in self.slack.get_tool_definitions():
            self._tools[tool_def.name] = tool_def
            self._handlers[tool_def.name] = self.slack

        for tool_def in self.gmail.get_tool_definitions():
            self._tools[tool_def.name] = tool_def
            self._handlers[tool_def.name] = self.gmail

        for tool_def in self.web.get_tool_definitions():
            self._tools[tool_def.name] = tool_def
            self._handlers[tool_def.name] = self.web

    def get_all_tool_definitions(self) -> List[MCPToolDefinition]:
        return list(self._tools.values())

    def get_tool_definition(self, name: str) -> Optional[MCPToolDefinition]:
        return self._tools.get(name)

    async def execute_tool(self, call: MCPToolCall, scope: Optional[PermissionScope] = None) -> MCPToolResult:
        handler = self._handlers.get(call.tool_name)
        if not handler:
            return MCPToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                success=False,
                data=None,
                error=f"No MCP server registered for tool '{call.tool_name}'"
            )

        tool_def = self._tools.get(call.tool_name)
        if tool_def and tool_def.requires_approval and (not scope or not scope.can_mutate):
            return MCPToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                success=False,
                data=None,
                error="Action halted: tool requires explicit human approval before execution",
                is_mutation=True
            )

        res = await handler.execute(call.tool_name, call.arguments, scope=scope)
        res.call_id = call.call_id
        return res

    async def execute_parallel(self, calls: List[MCPToolCall], scope: Optional[PermissionScope] = None) -> List[MCPToolResult]:
        """Execute independent tool calls concurrently."""
        tasks = [self.execute_tool(call, scope=scope) for call in calls]
        return await asyncio.gather(*tasks)
