"""End-to-end integration tests for ContextMesh Agent Graph."""

import pytest
from agent.graph import ContextMeshAgent


@pytest.mark.asyncio
async def test_agent_cross_source_query():
    agent = ContextMeshAgent()
    query = "What is blocking the authentication release, who owns each blocker, and what commitments were made in email?"
    
    response = await agent.run(query=query, thread_id="test_thread_01")
    assert response is not None
    assert response.plan is not None
    assert len(response.plan.steps) > 0
    assert len(response.citations) > 0
    assert "ATL-101" in response.answer or any("ATL-101" in c.evidence_id for c in response.citations)


@pytest.mark.asyncio
async def test_agent_mutation_approval_gate():
    agent = ContextMeshAgent()
    query = "Create a proposed Jira task for Redis session encryption"

    # Default can_mutate=False -> agent must halt and flag approval requirement
    response = await agent.run(query=query, thread_id="test_thread_02", can_mutate=False)
    assert response.requires_approval is True
    assert response.pending_mutation is not None
    assert "Approval Required" in response.answer


@pytest.mark.asyncio
async def test_agent_jit_auth_challenge():
    agent = ContextMeshAgent()
    query = "What did Priya commit to in email regarding the release?"

    # When allow_auth_gate=True, agent should halt and challenge for unauthenticated services
    response = await agent.run(query=query, thread_id="test_thread_auth", allow_auth_gate=True)
    assert response is not None
    assert response.auth_required is True
    assert len(response.missing_services) > 0
    assert "Authentication Required" in response.answer
    assert response.auth_challenge is not None
    assert "query" in response.auth_challenge


@pytest.mark.asyncio
async def test_agent_force_demo_bypasses_auth():
    agent = ContextMeshAgent()
    query = "What did Priya commit to in email regarding the release?"

    # When force_demo=True, agent should bypass the auth challenge and return synthetic evidence
    response = await agent.run(query=query, thread_id="test_thread_demo", force_demo=True, allow_auth_gate=True)
    assert response is not None
    assert response.auth_required is False
    assert len(response.citations) > 0


def test_agent_response_coercion():
    """Verify AgentResponse coerces dicts or foreign models into QueryPlan without validation errors."""
    from agent.state import AgentResponse, QueryPlan, PlanStep
    from pydantic import BaseModel

    class ForeignPlan(BaseModel):
        user_intent: str = "custom_foreign_intent"
        steps: list = []

    # Duck-typed foreign plan
    resp = AgentResponse(
        answer="ok",
        plan=ForeignPlan(),
        citations=[{"citation_id": "c1", "evidence_id": "e1", "claim": "cl", "source_url": "#", "source_type": "jira"}]
    )
    assert isinstance(resp.plan, QueryPlan)
    assert resp.plan.user_intent == "custom_foreign_intent"
    assert len(resp.citations) == 1
    assert resp.citations[0].claim == "cl"


