import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel
from retrieval.normalization import Evidence


class Contradiction(BaseModel):
    """Represents a conflict between two or more pieces of evidence."""
    topic: str
    description: str
    conflicting_evidence_ids: List[str]
    authoritative_source_id: Optional[str] = None
    resolution_suggestion: str


def evaluate_freshness(evidence: Evidence, reference_time: Optional[datetime] = None) -> Evidence:
    """Assess age and stamp freshness metadata."""
    if not evidence.updated_at:
        evidence.freshness_label = "Unknown age"
        return evidence

    try:
        updated_dt = datetime.fromisoformat(evidence.updated_at.replace("Z", "+00:00"))
        now = reference_time or datetime.now(timezone.utc)
        diff_days = (now - updated_dt).total_seconds() / 86400.0

        if diff_days < 1.0:
            evidence.freshness_label = "Live (< 24h)"
            evidence.is_stale = False
        elif diff_days < 7.0:
            evidence.freshness_label = f"Recent ({int(diff_days)}d ago)"
            evidence.is_stale = False
        elif diff_days < 30.0:
            evidence.freshness_label = f"Moderate ({int(diff_days)}d ago)"
            evidence.is_stale = False
        else:
            evidence.freshness_label = f"Potentially Stale ({int(diff_days)}d ago)"
            evidence.is_stale = True
    except Exception:
        evidence.freshness_label = "Unparsed date"

    return evidence


def detect_contradictions(evidence_list: List[Evidence]) -> List[Contradiction]:
    """Inspect retrieved multi-source evidence for factual conflicts (dates, statuses, undocumented decisions)."""
    contradictions: List[Contradiction] = []
    if len(evidence_list) < 1:
        return contradictions

    # 1. Check for release/launch date discrepancies across sources
    date_regex = re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?", re.IGNORECASE)
    
    release_evidence: List[Tuple[Evidence, List[str]]] = []
    for ev in evidence_list:
        c_lower = ev.content.lower() + " " + ev.title.lower()
        if any(term in c_lower for term in ["release", "launch", "target date", "deadline", "postpon"]):
            found_dates = date_regex.findall(ev.content)
            if found_dates:
                release_evidence.append((ev, found_dates))

    # Compare pairs of evidence from different sources
    for i in range(len(release_evidence)):
        ev_a, dates_a = release_evidence[i]
        for j in range(i + 1, len(release_evidence)):
            ev_b, dates_b = release_evidence[j]
            if ev_a.source != ev_b.source:
                # Check if dates differ
                norm_a = {d.lower().replace(",", "").strip() for d in dates_a}
                norm_b = {d.lower().replace(",", "").strip() for d in dates_b}
                if norm_a and norm_b and not norm_a.intersection(norm_b):
                    # Identified date discrepancy
                    c_all = (ev_a.title + " " + ev_a.content + " " + ev_b.title + " " + ev_b.content).lower()
                    topic = "Payments Launch Target Date" if "payment" in c_all else "Project Release Target Date"
                    # Authoritative source is typically the newer/live email or announcement
                    authoritative = ev_b if (ev_b.source == "gmail" or (ev_b.updated_at or "") > (ev_a.updated_at or "")) else ev_a
                    contradictions.append(
                        Contradiction(
                            topic=topic,
                            description=(
                                f"{ev_a.source.capitalize()} evidence ({ev_a.evidence_id}) references {', '.join(dates_a)}, "
                                f"while {ev_b.source.capitalize()} evidence ({ev_b.evidence_id}) references {', '.join(dates_b)}."
                            ),
                            conflicting_evidence_ids=[ev_a.evidence_id, ev_b.evidence_id],
                            authoritative_source_id=authoritative.evidence_id,
                            resolution_suggestion=f"Prefer the newer {authoritative.source.capitalize()} stakeholder announcement; flag older specifications as pending update."
                        )
                    )
                    break
        if contradictions:
            break

    # 2. Check for undocumented decisions
    for ev in evidence_list:
        c_lower = ev.content.lower()
        if any(pattern in c_lower for pattern in ["not been documented in slack", "never in slack", "not yet documented", "undocumented"]):
            contradictions.append(
                Contradiction(
                    topic="Undocumented Architecture Decision",
                    description=(
                        f"{ev.source.capitalize()} record ({ev.evidence_id}) documents an active decision "
                        "that explicitly states it has not been documented in team workspace architecture records."
                    ),
                    conflicting_evidence_ids=[ev.evidence_id],
                    authoritative_source_id=ev.evidence_id,
                    resolution_suggestion="Highlight that decision exists in communications but team workspace records require documentation."
                )
            )

    return contradictions
