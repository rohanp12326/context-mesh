"""MCP Server exposing standardized Notion operations."""

from typing import Any, Dict, List, Optional
from connectors.notion.connector import NotionConnector
from connectors.base import PermissionScope
from mcp_servers.base import MCPToolDefinition, MCPToolResult


class NotionMCPServer:
    """MCP interface for Notion service."""

    def __init__(self, connector: Optional[NotionConnector] = None):
        self.connector = connector or NotionConnector()

    def get_tool_definitions(self) -> List[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="notion.search_pages",
                description="Search Notion pages, documents, runbooks, and meeting notes.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search terms or page title"},
                        "limit": {"type": "integer", "description": "Max number of pages", "default": 10}
                    },
                    "required": ["query"]
                },
                is_mutation=False,
                requires_approval=False
            ),
            MCPToolDefinition(
                name="notion.get_page_content",
                description="Get full content of a specific Notion page by page ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Notion page ID"}
                    },
                    "required": ["page_id"]
                },
                is_mutation=False,
                requires_approval=False
            )
        ]

    async def execute(self, tool_name: str, args: Dict[str, Any], scope: Optional[PermissionScope] = None) -> MCPToolResult:
        try:
            if tool_name == "notion.search_pages":
                query = args.get("query", "")
                limit = int(args.get("limit", 10))
                items = await self.connector.search(query=query, limit=limit, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=[i.model_dump() for i in items])

            elif tool_name == "notion.get_page_content":
                page_id = args.get("page_id", "")
                item = await self.connector.get_by_id(page_id, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=item.model_dump() if item else None)

            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")
        except Exception as e:
            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=str(e))
