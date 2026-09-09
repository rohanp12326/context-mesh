"""Unit tests for tiered memory subsystem (short-term & durable long-term)."""

import pytest
from memory.short_term import ShortTermMemoryStore
from memory.long_term import LongTermMemoryStore, MemoryRecord
from memory.promotion import MemoryPromotionEngine


def test_short_term_thread_state():
    store = ShortTermMemoryStore()
    state = store.get_or_create("thread-42")
    assert state.thread_id == "thread-42"
    assert len(state.messages) == 0

    store.append_message("thread-42", "user", "Hello ContextMesh")
    assert len(store.get_or_create("thread-42").messages) == 1


def test_long_term_defaults_and_search():
    store = LongTermMemoryStore()
    records = store.search_by_entity("Atlas")
    assert len(records) > 0
    atlas_alias = records[0]
    assert atlas_alias.content.get("jira_project") == "ATL"


def test_memory_promotion_and_superseding():
    store = LongTermMemoryStore()
    engine = MemoryPromotionEngine(store)

    # Validate safety filter rejects secrets
    safe, reason = engine.is_safe_and_promotable("User provided password: supersecret123")
    assert safe is False
    assert "sensitive" in reason

    # Promote alias and ensure older alias is marked superseded
    new_alias = engine.promote_alias_candidate(alias="Atlas", jira_project="ATL-PROJ")
    records = store.search_by_entity("Atlas")
    
    # Check that new alias is active
    active = [r for r in records if r.superseded_by is None]
    assert len(active) == 1
    assert active[0].content.get("jira_project") == "ATL-PROJ"
