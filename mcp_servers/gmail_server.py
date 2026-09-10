"""MCP Server exposing standardized Gmail operations."""

from typing import Any, Dict, List, Optional
from connectors.gmail.connector import GmailConnector
from connectors.base import PermissionScope
from mcp_servers.base import MCPToolDefinition, MCPToolResult


class GmailMCPServer:
    """MCP interface for Gmail service."""

    def __init__(self, connector: Optional[GmailConnector] = None):
        self.connector = connector or GmailConnector()

    def get_tool_definitions(self) -> List[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="gmail.search_messages",
                description="Search Gmail email threads and messages for discussions, approvals, and commitments.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query terms or subject"},
                        "limit": {"type": "integer", "description": "Max number of threads", "default": 10}
                    },
                    "required": ["query"]
                },
                is_mutation=False,
                requires_approval=False
            ),
            MCPToolDefinition(
                name="gmail.get_thread",
                description="Get full content of an email thread by thread ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string", "description": "Email thread ID"}
                    },
                    "required": ["thread_id"]
                },
                is_mutation=False,
                requires_approval=False
            )
        ]

    async def execute(self, tool_name: str, args: Dict[str, Any], scope: Optional[PermissionScope] = None) -> MCPToolResult:
        try:
            if tool_name == "gmail.search_messages":
                query = args.get("query", "")
                limit = int(args.get("limit", 10))
                items = await self.connector.search(query=query, limit=limit, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=[i.model_dump() for i in items])

            elif tool_name == "gmail.get_thread":
                thread_id = args.get("thread_id", "")
                item = await self.connector.get_by_id(thread_id, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=item.model_dump() if item else None)

            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")
        except Exception as e:
            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=str(e))
