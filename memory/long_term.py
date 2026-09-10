"""Structured durable long-term memory store."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class MemoryRecord(BaseModel):
    """A durable long-term memory entry."""
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    namespace: List[str]  # e.g. ["user-default", "project-atlas"]
    type: Literal["project_alias", "user_preference", "role_mapping", "confirmed_decision", "stable_terminology"]
    content: Dict[str, Any]
    source: Dict[str, str] = Field(default_factory=lambda: {"kind": "system_extracted", "reference": "conversation"})
    confidence: float = 1.0
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_verified_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None
    superseded_by: Optional[str] = None


class LongTermMemoryStore:
    """In-memory and persistent durable memory store."""

    def __init__(self):
        self._records: Dict[str, MemoryRecord] = {}
        self._init_defaults()

    def _init_defaults(self):
        """Seed default enterprise knowledge (e.g. Atlas alias)."""
        atlas_alias = MemoryRecord(
            memory_id="mem-atlas-alias-01",
            namespace=["global", "project-atlas"],
            type="project_alias",
            content={
                "alias": "Atlas",
                "jira_project": "ATL",
                "slack_channel": "proj-atlas-release",
                "slack_canvas_id": "slack-atlas-spec",
                "team_lead": "sarah.jenkins@company.com"
            },
            source={"kind": "user_confirmed", "reference": "seed"},
            confidence=1.0
        )
        self.add_record(atlas_alias)

        marcus_role = MemoryRecord(
            memory_id="mem-role-marcus-01",
            namespace=["global", "people"],
            type="role_mapping",
            content={
                "name": "Marcus Vance",
                "email": "marcus.vance@company.com",
                "domain": "Authentication & Security",
                "leads": ["auth-service"]
            },
            source={"kind": "user_confirmed", "reference": "seed"},
            confidence=1.0
        )
        self.add_record(marcus_role)

        priya_role = MemoryRecord(
            memory_id="mem-role-priya-01",
            namespace=["global", "people"],
            type="role_mapping",
            content={
                "name": "Priya Sharma",
                "email": "priya.sharma@company.com",
                "domain": "Payments Gateway & Stripe Integration",
                "leads": ["payments-service"]
            },
            source={"kind": "user_confirmed", "reference": "seed"},
            confidence=1.0
        )
        self.add_record(priya_role)

    def add_record(self, record: MemoryRecord) -> str:
        self._records[record.memory_id] = record
        return record.memory_id

    def get_record(self, memory_id: str) -> Optional[MemoryRecord]:
        return self._records.get(memory_id)

    def delete_record(self, memory_id: str) -> bool:
        if memory_id in self._records:
            del self._records[memory_id]
            return True
        return False

    def list_records(self, namespace_prefix: Optional[str] = None, include_superseded: bool = False) -> List[MemoryRecord]:
        results = []
        for r in self._records.values():
            if not include_superseded and r.superseded_by is not None:
                continue
            if namespace_prefix:
                if any(ns.startswith(namespace_prefix) for ns in r.namespace):
                    results.append(r)
            else:
                results.append(r)
        return results

    def search_by_entity(self, entity_name: str) -> List[MemoryRecord]:
        """Search durable memories referencing a specific entity or alias."""
        matched = []
        name_lower = entity_name.lower()
        for r in self.list_records(include_superseded=False):
            for k, v in r.content.items():
                if isinstance(v, str) and (name_lower in v.lower() or v.lower() in name_lower):
                    matched.append(r)
                    break
        return matched

    def supersede(self, old_memory_id: str, new_memory_id: str):
        """Mark an old memory record as superseded by a newer verified record."""
        old = self._records.get(old_memory_id)
        if old:
            old.superseded_by = new_memory_id
