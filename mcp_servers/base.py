"""Base MCP tool schema and dispatch definitions."""

from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field



class MCPToolDefinition(BaseModel):
    name: str
    description: str
    inputSchema: Dict[str, Any]
    is_mutation: bool = False
    requires_approval: bool = False


class MCPToolCall(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    call_id: Optional[str] = None


class MCPToolResult(BaseModel):
    call_id: Optional[str] = None
    tool_name: str
    success: bool
    data: Any
    error: Optional[str] = None
    is_mutation: bool = False
