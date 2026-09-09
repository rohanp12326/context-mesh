"""Memory candidate extraction, filtering, and promotion policies."""

import re
from typing import Any, Dict, List, Optional, Tuple
from memory.long_term import LongTermMemoryStore, MemoryRecord


# Sensitive terms and transient status regexes to reject from durable storage
FORBIDDEN_KEYWORDS = [
    "password", "secret", "bearer ", "token", "private_key",
    "access_key", "api_key", "ssn", "credit_card"
]

TRANSIENT_STATUS_TERMS = [
    "in progress", "in review", "in dev", "sprint 24", "yesterday", "tomorrow"
]


class MemoryPromotionEngine:
    """Evaluates candidate memories and promotes eligible facts to long-term storage."""

    def __init__(self, long_term_store: LongTermMemoryStore):
        self.store = long_term_store

    def is_safe_and_promotable(self, candidate_text: str) -> Tuple[bool, str]:
        """Validate candidate against security, privacy, and permanence rules."""
        candidate_lower = candidate_text.lower()

        # Check for secrets or sensitive data
        for kw in FORBIDDEN_KEYWORDS:
            if kw in candidate_lower:
                return False, f"Candidate contains sensitive/forbidden keyword '{kw}'"

        # Reject ephemeral/transient statuses
        for term in TRANSIENT_STATUS_TERMS:
            if f"status: {term}" in candidate_lower or f"is currently {term}" in candidate_lower:
                return False, "Candidate reflects transient status rather than durable knowledge"

        return True, "Valid candidate"

    def promote_alias_candidate(
        self,
        alias: str,
        jira_project: str,
        namespace: Optional[List[str]] = None,
        source_ref: str = "conversation"
    ) -> MemoryRecord:
        """Promote a project alias mapping to durable storage, superseding older mappings."""
        ns = namespace or ["global", f"project-{alias.lower()}"]
        
        # Check if an older alias exists
        existing = self.store.search_by_entity(alias)
        new_record = MemoryRecord(
            namespace=ns,
            type="project_alias",
            content={"alias": alias, "jira_project": jira_project},
            source={"kind": "user_confirmed", "reference": source_ref},
            confidence=1.0
        )
        self.store.add_record(new_record)

        for old in existing:
            if old.type == "project_alias" and old.content.get("alias", "").lower() == alias.lower():
                self.store.supersede(old.memory_id, new_record.memory_id)

        return new_record

    def promote_preference(
        self,
        user_id: str,
        preference_key: str,
        preference_value: Any,
        source_ref: str = "user_input"
    ) -> MemoryRecord:
        """Store a user preference durable memory."""
        record = MemoryRecord(
            namespace=[f"user-{user_id}", "preferences"],
            type="user_preference",
            content={preference_key: preference_value},
            source={"kind": "user_confirmed", "reference": source_ref},
            confidence=1.0
        )
        self.store.add_record(record)
        return record
