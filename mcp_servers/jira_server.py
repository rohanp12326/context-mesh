"""MCP Server exposing standardized Jira operations."""

from typing import Any, Dict, List, Optional
from connectors.jira.connector import JiraConnector
from connectors.base import PermissionScope
from mcp_servers.base import MCPToolDefinition, MCPToolResult


class JiraMCPServer:
    """MCP interface for Jira service."""

    def __init__(self, connector: Optional[JiraConnector] = None):
        self.connector = connector or JiraConnector()

    def get_tool_definitions(self) -> List[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="jira.search_issues",
                description="Search Jira issues using JQL syntax or keywords (e.g. project, assignee, blockers).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "JQL query or search terms"},
                        "limit": {"type": "integer", "description": "Maximum number of issues", "default": 10}
                    },
                    "required": ["query"]
                },
                is_mutation=False,
                requires_approval=False
            ),
            MCPToolDefinition(
                name="jira.get_issue",
                description="Get detailed information for a specific Jira issue by issue key (e.g. ATL-101).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Jira issue key"}
                    },
                    "required": ["issue_key"]
                },
                is_mutation=False,
                requires_approval=False
            ),
            MCPToolDefinition(
                name="jira.create_issue",
                description="Create a new Jira issue/task. MUTATION OPERATION: requires explicit human approval.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project key (e.g. ATL)"},
                        "summary": {"type": "string", "description": "Issue title/summary"},
                        "description": {"type": "string", "description": "Issue description details", "default": ""},
                        "priority": {"type": "string", "description": "Issue priority", "default": "Medium"}
                    },
                    "required": ["project", "summary"]
                },
                is_mutation=True,
                requires_approval=True
            )
        ]

    async def execute(self, tool_name: str, args: Dict[str, Any], scope: Optional[PermissionScope] = None) -> MCPToolResult:
        try:
            if tool_name == "jira.search_issues":
                query = args.get("query", "")
                limit = int(args.get("limit", 10))
                items = await self.connector.search(query=query, limit=limit, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=[i.model_dump() for i in items])

            elif tool_name == "jira.get_issue":
                key = args.get("issue_key", "")
                item = await self.connector.get_by_id(key, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=item.model_dump() if item else None)

            elif tool_name == "jira.create_issue":
                res = await self.connector.mutate(action="create_issue", params=args, scope=scope)
                return MCPToolResult(tool_name=tool_name, success=True, data=res, is_mutation=True)

            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")
        except Exception as e:
            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=str(e))
