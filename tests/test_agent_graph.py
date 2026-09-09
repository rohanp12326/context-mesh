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
