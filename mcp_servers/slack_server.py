"""MCP Server exposing standardized Slack operations."""

from typing import Any, Dict, List, Optional
from connectors.slack.connector import SlackConnector
from connectors.base import PermissionScope
from mcp_servers.base import MCPToolDefinition, MCPToolResult


class SlackMCPServer:
    """MCP interface for Slack service."""

    def __init__(self, connector: Optional[SlackConnector] = None):
        self.connector = connector or SlackConnector()

    def get_tool_definitions(self) -> List[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="slack.search_messages",
                description=(
                    "Search Slack messages, channels, announcements, discussions, and canvases. "
                    "Supports Slack search operators: use 'from:@user' or 'from:<name>' to find messages sent by a specific user, "
                    "'to:me' or 'to:<user>' for messages sent to someone, 'in:#channel' for specific channels, or keywords."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query terms or Slack search syntax (e.g. 'from:@alice', 'in:#general', 'roadmap')"
                        },
                        "limit": {"type": "integer", "description": "Max number of messages", "default": 10}
                    },
                    "required": ["query"]
                },
                is_mutation=False,
                requires_approval=False
            ),
            MCPToolDefinition(
                name="slack.get_thread",
                description="Get full content of a Slack thread or discussion by ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string", "description": "Slack thread ID or timestamp"}
                    },
                    "required": ["thread_id"]
                },
                is_mutation=False,
                requires_approval=False
            ),
            MCPToolDefinition(
                name="slack.post_message",
                description="Post a message to a Slack channel (MUTATION - requires approval).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Slack channel name or ID (e.g. general, C0123)"},
                        "text": {"type": "string", "description": "Message text to post"}
                    },
                    "required": ["channel", "text"]
                },
                is_mutation=True,
                requires_approval=True
            )
        ]

    async def execute(self, tool_name: str, args: Dict[str, Any], scope: Optional[PermissionScope] = None) -> MCPToolResult:
        try:
            if tool_name == "slack.search_messages":
                query = args.get("query", "")
                limit = int(args.get("limit", 10))
                items = await self.connector.search(query=query, limit=limit, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=[i.model_dump() for i in items])

            elif tool_name == "slack.get_thread":
                thread_id = args.get("thread_id", "")
                item = await self.connector.get_by_id(thread_id, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=item.model_dump() if item else None)

            elif tool_name == "slack.post_message":
                res = await self.connector.mutate("post_message", args, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=res, is_mutation=True)

            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")
        except Exception as e:
            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=str(e))
