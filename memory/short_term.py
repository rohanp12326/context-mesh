"""Short-term thread-scoped memory checkpointer."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class ThreadState(BaseModel):
    """Conversation-scoped working memory."""
    thread_id: str
    user_id: str = "default_user"
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    rolling_summary: str = ""
    resolved_entities: Dict[str, str] = Field(default_factory=dict)
    active_plan: Optional[Dict[str, Any]] = None
    pending_approvals: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ShortTermMemoryStore:
    """Thread state store with in-memory storage and optional PostgreSQL persistence."""

    def __init__(self):
        self._threads: Dict[str, ThreadState] = {}

    def get_or_create(self, thread_id: str, user_id: str = "default_user") -> ThreadState:
        if thread_id not in self._threads:
            self._threads[thread_id] = ThreadState(thread_id=thread_id, user_id=user_id)
        return self._threads[thread_id]

    def save_state(self, state: ThreadState):
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._threads[state.thread_id] = state

    def append_message(self, thread_id: str, role: str, content: str):
        state = self.get_or_create(thread_id)
        state.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        self.save_state(state)

    def set_pending_approval(self, thread_id: str, approval_payload: Dict[str, Any]):
        state = self.get_or_create(thread_id)
        state.pending_approvals.append(approval_payload)
        self.save_state(state)

    def clear_pending_approvals(self, thread_id: str):
        state = self.get_or_create(thread_id)
        state.pending_approvals = []
        self.save_state(state)
