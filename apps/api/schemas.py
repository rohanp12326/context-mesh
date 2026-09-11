"""FastAPI request and response schemas."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from agent.state import AgentResponse, Citation, QueryPlan
from retrieval.freshness import Contradiction
from memory.long_term import MemoryRecord


class ChatRequest(BaseModel):
    query: str
    user_id: str = "default_user"
    thread_id: str = "default_thread"
    can_mutate: bool = False
    force_demo: bool = False
    allow_auth_gate: bool = False



class ApprovalRequest(BaseModel):
    thread_id: str
    approved: bool
    mutation: Dict[str, Any]


class MemoryCreateRequest(BaseModel):
    namespace: List[str]
    type: str
    content: Dict[str, Any]


class IntegrationConfigureRequest(BaseModel):
    service: str  # zai | jira | slack | gmail | composio
    credentials: Dict[str, Any]


class IntegrationTestRequest(BaseModel):
    service: str
    credentials: Dict[str, Any]


class ComposioLinkRequest(BaseModel):
    toolkit: str  # jira | slack | gmail
    user_id: str = "default_user"
    callback_url: Optional[str] = None


class ComposioStatusRequest(BaseModel):
    toolkit: Optional[str] = None
    user_id: str = "default_user"


class HealthResponse(BaseModel):
    status: str
    version: str
    connector_mode: str
    llm_provider: str
