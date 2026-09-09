"""Hierarchical execution trace builder for agentic request tracking."""

import time
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from observability.redaction import sanitize_payload


class TraceSpan(BaseModel):
    span_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    start_time: float = Field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    status: str = "ok"  # ok | error
    attributes: Dict[str, Any] = Field(default_factory=dict)
    children: List["TraceSpan"] = Field(default_factory=list)

    def finish(self, status: str = "ok"):
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000.0, 2)
        self.status = status


class RequestTrace(BaseModel):
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    root_span: TraceSpan
    user_id: str = "default_user"
    thread_id: str = "default_thread"
    total_duration_ms: Optional[float] = None


class TraceRecorder:
    """In-memory trace collector for request-level observability."""

    def __init__(self):
        self._traces: Dict[str, RequestTrace] = {}

    def start_trace(self, request_name: str = "agent_request", user_id: str = "default_user", thread_id: str = "default_thread") -> RequestTrace:
        trace_id = str(uuid.uuid4())
        root = TraceSpan(name=request_name)
        trace = RequestTrace(trace_id=trace_id, root_span=root, user_id=user_id, thread_id=thread_id)
        self._traces[trace_id] = trace
        return trace

    def add_span(self, parent_span: TraceSpan, name: str, attributes: Optional[Dict[str, Any]] = None) -> TraceSpan:
        clean_attrs = sanitize_payload(attributes or {})
        span = TraceSpan(name=name, attributes=clean_attrs)
        parent_span.children.append(span)
        return span

    def finish_trace(self, trace_id: str):
        trace = self._traces.get(trace_id)
        if trace:
            trace.root_span.finish()
            trace.total_duration_ms = trace.root_span.duration_ms

    def get_trace(self, trace_id: str) -> Optional[RequestTrace]:
        return self._traces.get(trace_id)


# Global trace recorder singleton
GLOBAL_TRACER = TraceRecorder()
