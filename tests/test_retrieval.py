"""Unit tests for evidence normalization, freshness, and ranking."""

import pytest
from connectors.base import ConnectorItem
from retrieval.normalization import normalize_connector_item, normalize_evidence_list
from retrieval.freshness import detect_contradictions, evaluate_freshness
from retrieval.ranking import rank_evidence, check_sufficiency


def test_evidence_normalization():
    item = ConnectorItem(
        source="jira",
        id="ATL-101",
        title="[ATL-101] Fix OAuth2 race condition",
        content="Details about race condition",
        url="https://jira.example.com/browse/ATL-101",
        updated_at="2026-09-08T10:00:00Z"
    )
    ev = normalize_connector_item(item)
    assert ev.evidence_id == "jira:ATL-101"
    assert ev.content_hash is not None
    assert len(ev.content_hash) > 0


def test_freshness_evaluation():
    item = ConnectorItem(
        source="jira",
        id="ATL-101",
        title="Sample",
        content="Details",
        url="https://jira.example.com",
        updated_at="2026-09-08T10:00:00Z"
    )
    ev = normalize_connector_item(item)
    evaluated = evaluate_freshness(ev)
    assert evaluated.freshness_label != "Unparsed date"


def test_contradiction_detection():
    notion_item = normalize_connector_item(ConnectorItem(
        source="notion",
        id="spec",
        title="Atlas Spec",
        content="Target Release Date: September 10, 2026",
        url="https://notion.so/spec"
    ))
    email_item = normalize_connector_item(ConnectorItem(
        source="gmail",
        id="th-01",
        title="Payments delayed",
        content="Postponing launch to Sept 18 due to Stripe bug",
        url="https://gmail.com/th-01"
    ))

    contradictions = detect_contradictions([notion_item, email_item])
    assert len(contradictions) > 0
    assert "Payments Launch Target Date" in contradictions[0].topic


def test_evidence_ranking():
    ev1 = normalize_connector_item(ConnectorItem(
        source="jira",
        id="ATL-101",
        title="Fix token refresh blocker",
        content="Blocks authentication release",
        url="https://jira.example.com",
        metadata={"is_blocker": True}
    ))
    ev2 = normalize_connector_item(ConnectorItem(
        source="jira",
        id="ATL-103",
        title="Update runbook",
        content="Docs updates",
        url="https://jira.example.com"
    ))

    ranked = rank_evidence("authentication release blocker", [ev2, ev1], top_k=2)
    assert ranked[0].evidence_id == "jira:ATL-101"
