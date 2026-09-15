"""Types and data structures for LLM function calling and ReAct agent loop."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ToolCallRequest(BaseModel):
    """Structured tool call produced by an LLM."""
    id: str = Field(default_factory=lambda: "call_default")
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """Normalized response from LLM supporting tool calling and direct answers."""
    content: Optional[str] = None
    thought: Optional[str] = None
    tool_calls: List[ToolCallRequest] = Field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "length"
    raw_payload: Optional[Dict[str, Any]] = None
