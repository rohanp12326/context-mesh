"""Relevance ranking, deduplication, and sufficiency checking for retrieved evidence."""

import re
from typing import List, Tuple
from retrieval.normalization import Evidence


STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll",
    "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself",
    "let's", "me", "more", "most", "my", "myself", "no", "nor", "not", "of", "off",
    "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out",
    "over", "own", "same", "she", "she'd", "she'll", "she's", "should", "shouldn't",
    "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll",
    "they're", "they've", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which", "while",
    "who", "who's", "whom", "why", "why's", "with", "won't", "would", "wouldn't",
    "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves"
}

URGENCY_KEYWORDS = {"urgent", "urgernt", "critical", "blocker", "blocking", "p0", "p1", "high", "priority", "asap"}


def score_evidence(query: str, evidence: Evidence) -> float:
    """Calculate a relevance score for an evidence piece given the query."""
    score = 0.0
    raw_query_words = set(re.findall(r"\w+", query.lower()))
    content_words = set(re.findall(r"\w+", evidence.content.lower()))
    title_words = set(re.findall(r"\w+", evidence.title.lower()))

    # Filter stopwords for meaningful content overlap
    filtered_query_words = {w for w in raw_query_words if w not in STOP_WORDS and len(w) > 1}
    query_words = filtered_query_words if filtered_query_words else raw_query_words
    if not query_words:
        return 1.0

    # Author match bonus (When user asks about a person or sender, messages authored by that person must be prioritized!)
    author_words = set(re.findall(r"\w+", (evidence.author or "").lower()))
    author_overlap = len(query_words.intersection(author_words))
    is_sender_query = any(w in raw_query_words for w in ["from", "by", "sent", "author", "authored"]) or bool(
        re.search(r"\b(?:does|has|is)\s+\w+\s+(?:have|has|sent|post)", query.lower())
    )
    if author_overlap:
        score += author_overlap * (7.0 if is_sender_query else 5.0)

    # Title match bonus
    title_overlap = len(query_words.intersection(title_words))
    score += title_overlap * 3.5

    # Content match
    content_overlap = len(query_words.intersection(content_words))
    score += content_overlap * 1.2

    # Freshness / Recency gradient
    freshness_label = getattr(evidence, "freshness_label", "")
    if freshness_label == "Live":
        score += 2.5
    elif freshness_label == "Recent":
        score += 2.0
    elif not evidence.is_stale:
        score += 1.5
    else:
        score -= 0.5

    # Blocker/Priority/Urgency bonus
    has_urgency = bool(raw_query_words.intersection(URGENCY_KEYWORDS))
    if evidence.source == "jira":
        is_blocker = bool(evidence.metadata.get("is_blocker"))
        prio = str(evidence.metadata.get("priority", "")).lower()
        if is_blocker:
            score += 3.0 if has_urgency else 2.0
        if prio in ("high", "highest", "critical", "p0", "p1") and has_urgency:
            score += 2.5

    return max(0.1, score)


def rank_evidence(query: str, evidence_list: List[Evidence], top_k: int = 15) -> List[Evidence]:
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

    if "slack" in q_lower or "spec" in q_lower or "doc" in q_lower:
        if "slack" not in sources_present:
            return False, "Query asks for specifications/Slack, but no Slack evidence was retrieved."

    return True, "Retrieved evidence appears sufficient."
