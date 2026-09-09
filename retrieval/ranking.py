"""Relevance ranking, deduplication, and sufficiency checking for retrieved evidence."""

import re
from typing import List, Tuple
from retrieval.normalization import Evidence


def score_evidence(query: str, evidence: Evidence) -> float:
    """Calculate a relevance score for an evidence piece given the query."""
    score = 0.0
    query_words = set(re.findall(r"\w+", query.lower()))
    if not query_words:
        return 1.0

    # Title match bonus
    title_words = set(re.findall(r"\w+", evidence.title.lower()))
    title_overlap = len(query_words.intersection(title_words))
    score += title_overlap * 3.0

    # Content match
    content_words = set(re.findall(r"\w+", evidence.content.lower()))
    content_overlap = len(query_words.intersection(content_words))
    score += content_overlap * 1.0

    # Freshness bonus
    if not evidence.is_stale:
        score += 1.5

    # Blocker/Priority bonus for Jira
    if evidence.source == "jira":
        if evidence.metadata.get("is_blocker"):
            score += 2.0

    return score


def rank_evidence(query: str, evidence_list: List[Evidence], top_k: int = 8) -> List[Evidence]:
    """Rank evidence items by relevance score and return the top_k."""
    scored: List[Tuple[float, Evidence]] = []
    for ev in evidence_list:
        s = score_evidence(query, ev)
        scored.append((s, ev))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:top_k]]


def check_sufficiency(query: str, evidence_list: List[Evidence]) -> Tuple[bool, str]:
    """Inspect whether retrieved evidence is sufficient to answer the prompt."""
    if not evidence_list:
        return False, "No evidence was retrieved."

    q_lower = query.lower()
    sources_present = {ev.source for ev in evidence_list}

    # Cross-source checks
    if "email" in q_lower or "commit" in q_lower:
        if "gmail" not in sources_present:
            return False, "Query asks for commitments/email, but no Gmail evidence was retrieved."

    if "block" in q_lower or "jira" in q_lower or "ticket" in q_lower:
        if "jira" not in sources_present:
            return False, "Query asks for blockers/tickets, but no Jira evidence was retrieved."

    if "notion" in q_lower or "spec" in q_lower or "doc" in q_lower:
        if "notion" not in sources_present:
            return False, "Query asks for specifications/Notion, but no Notion evidence was retrieved."

    return True, "Retrieved evidence appears sufficient."
