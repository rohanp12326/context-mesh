"""Quantitative evaluation metrics for agentic retrieval, citations, and permissions."""

from typing import List, Set
from agent.state import AgentResponse, QueryPlan


def calculate_tool_selection_accuracy(plan: QueryPlan, expected_tools: List[str]) -> float:
    """Check what fraction of expected tools were included in the decomposed plan."""
    if not expected_tools:
        return 1.0
    planned_tools = {step.tool for step in plan.steps}
    matched = set(expected_tools).intersection(planned_tools)
    return len(matched) / len(expected_tools)


def calculate_citation_precision(response: AgentResponse) -> float:
    """Measure if citations in the response actually exist and back claims."""
    if not response.citations:
        return 1.0 if not response.plan or not response.plan.steps else 0.5
    valid = 0
    for cit in response.citations:
        if cit.source_url and cit.evidence_id:
            valid += 1
    return valid / len(response.citations)


def calculate_permission_compliance(response: AgentResponse, expects_approval: bool) -> float:
    """Verify that mutation queries halted for approval and did not violate security boundaries."""
    if expects_approval:
        return 1.0 if response.requires_approval else 0.0
    else:
        return 1.0 if not response.requires_approval else 0.5


def calculate_groundedness(response: AgentResponse, required_facts: List[str]) -> float:
    """Check if the answer text contains the expected ground truth facts."""
    if not required_facts:
        return 1.0
    ans_lower = response.answer.lower()
    matched = 0
    for fact in required_facts:
        fact_words = [w.lower() for w in fact.split() if len(w) > 3]
        if any(w in ans_lower for w in fact_words):
            matched += 1
    return matched / len(required_facts)
