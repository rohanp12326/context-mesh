"""Typed agent state and plan definitions."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, ConfigDict
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
    model_config = ConfigDict(arbitrary_types_allowed=True)
    user_intent: str
    entities: Dict[str, str] = Field(default_factory=dict)
    steps: List[PlanStep] = Field(default_factory=list)
    risk_level: str = "low"  # low | medium | high
    requires_approval: bool = False

    @field_validator("steps", mode="before")
    @classmethod
    def coerce_steps(cls, v: Any) -> Any:
        if isinstance(v, list):
            res = []
            for item in v:
                if hasattr(item, "model_dump") and not isinstance(item, PlanStep):
                    res.append(item.model_dump())
                else:
                    res.append(item)
            return res
        return v


class Citation(BaseModel):
    citation_id: str
    evidence_id: str
    claim: str
    source_url: str
    source_type: str
    timestamp: Optional[str] = None


class ReActStep(BaseModel):
    iteration: int
    thought: str
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    duration_ms: Optional[float] = None


class AgentResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    confidence: float = 0.95
    plan: Optional[QueryPlan] = None
    contradictions: List[Contradiction] = Field(default_factory=list)
    trace_id: Optional[str] = None
    requires_approval: bool = False
    pending_mutation: Optional[Dict[str, Any]] = None
    auth_required: bool = False
    missing_services: List[str] = Field(default_factory=list)
    required_services: List[str] = Field(default_factory=list)
    skipped_services: List[str] = Field(default_factory=list)
    auth_challenge: Optional[Dict[str, Any]] = None
    react_steps: List[ReActStep] = Field(default_factory=list)
    tool_failures: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("react_steps", mode="before")
    @classmethod
    def coerce_react_steps(cls, v: Any) -> Any:
        if isinstance(v, list):
            res = []
            for item in v:
                if hasattr(item, "model_dump") and not isinstance(item, ReActStep):
                    res.append(item.model_dump())
                else:
                    res.append(item)
            return res
        return v

    @field_validator("plan", mode="before")
    @classmethod
    def coerce_plan(cls, v: Any) -> Any:
        if v is not None and not isinstance(v, (dict, QueryPlan)):
            if hasattr(v, "model_dump"):
                return v.model_dump()
            if hasattr(v, "__dict__"):
                return v.__dict__
        return v

    @field_validator("citations", mode="before")
    @classmethod
    def coerce_citations(cls, v: Any) -> Any:
        if isinstance(v, list):
            res = []
            for item in v:
                if hasattr(item, "model_dump") and not isinstance(item, Citation):
                    res.append(item.model_dump())
                else:
                    res.append(item)
            return res
        return v

    @field_validator("contradictions", mode="before")
    @classmethod
    def coerce_contradictions(cls, v: Any) -> Any:
        if isinstance(v, list):
            res = []
            for item in v:
                if hasattr(item, "model_dump") and not isinstance(item, Contradiction):
                    res.append(item.model_dump())
                else:
                    res.append(item)
            return res
        return v



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
