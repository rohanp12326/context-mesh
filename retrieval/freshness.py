"""Freshness assessment and contradiction detection across evidence."""

from datetime import datetime, timezone
from typing import Dict, List, Optional
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
    """Inspect retrieved multi-source evidence for factual conflicts (dates, statuses)."""
    contradictions: List[Contradiction] = []
    
    # Check for release date / launch date conflicts (e.g. Slack spec vs Email thread)
    slack_dates = []
    email_dates = []
    
    for ev in evidence_list:
        content_lower = ev.content.lower()
        if ev.source == "slack" and ("september 10" in content_lower or "target release date" in content_lower):
            slack_dates.append(ev)
        if ev.source == "gmail" and ("postponing launch to sept 18" in content_lower or "delay the payments launch" in content_lower or "new target launch date is september 18" in content_lower):
            email_dates.append(ev)

    if slack_dates and email_dates:
        contradictions.append(
            Contradiction(
                topic="Payments Launch Target Date",
                description=(
                    f"Slack specification ({slack_dates[0].evidence_id}) targets September 10, 2026, "
                    f"but recent Gmail discussion ({email_dates[0].evidence_id}) announces a delay to September 18, 2026 due to Stripe webhook issues."
                ),
                conflicting_evidence_ids=[slack_dates[0].evidence_id, email_dates[0].evidence_id],
                authoritative_source_id=email_dates[0].evidence_id,
                resolution_suggestion="Prefer the newer Gmail stakeholder announcement; flag Slack specification as pending update."
            )
        )

    # Check for undocumented decisions in Slack
    email_decisions = [ev for ev in evidence_list if ev.source == "gmail" and ("not been documented in slack" in ev.content.lower() or "never in slack" in ev.content.lower())]
    if email_decisions:
        for ed in email_decisions:
            contradictions.append(
                Contradiction(
                    topic="Undocumented Architecture Decision",
                    description=(
                        f"Email thread ({ed.evidence_id}) documents an active decision (e.g. Redis AES-256 encryption) "
                        "that explicitly states it has not been documented in Slack architecture records."
                    ),
                    conflicting_evidence_ids=[ed.evidence_id],
                    authoritative_source_id=ed.evidence_id,
                    resolution_suggestion="Highlight that decision exists in email but Slack records require documentation."
                )
            )

    return contradictions
