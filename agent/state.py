"""Typed agent state and plan definitions."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from retrieval.normalization import Evidence
from retrieval.freshness import Contradiction


class PlanStep(BaseModel):
    id: str
    tool: str
    query: str
    purpose: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)


class QueryPlan(BaseModel):
    user_intent: str
    entities: Dict[str, str] = Field(default_factory=dict)
    steps: List[PlanStep] = Field(default_factory=list)
    risk_level: str = "low"  # low | medium | high
    requires_approval: bool = False


class Citation(BaseModel):
    citation_id: str
    evidence_id: str
    claim: str
    source_url: str
    source_type: str
    timestamp: Optional[str] = None


class AgentResponse(BaseModel):
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    confidence: float = 0.95
    plan: Optional[QueryPlan] = None
    contradictions: List[Contradiction] = Field(default_factory=list)
    trace_id: Optional[str] = None
    requires_approval: bool = False
    pending_mutation: Optional[Dict[str, Any]] = None


class AgentState(BaseModel):
    """Complete working state for a single agent session/query."""
    thread_id: str = "default_thread"
    user_id: str = "default_user"
    user_query: str
    resolved_entities: Dict[str, str] = Field(default_factory=dict)
    plan: Optional[QueryPlan] = None
    raw_tool_results: List[Dict[str, Any]] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    contradictions: List[Contradiction] = Field(default_factory=list)
    final_response: Optional[AgentResponse] = None
    trace_id: Optional[str] = None
