"""End-to-end integration tests for ContextMesh Agent Graph."""

import pytest
from unittest.mock import AsyncMock
from agent.graph import ContextMeshAgent
from agent.llm_types import LLMResponse, ToolCallRequest
from connectors.base import ConnectorItem


@pytest.mark.asyncio
async def test_agent_cross_source_query(monkeypatch):
    agent = ContextMeshAgent()

    mock_jira_items = [
        ConnectorItem(
            source="jira",
            id="PROJ-101",
            title="[PROJ-101] Fix authentication blocker",
            content="Status: In Progress | Assignee: Alex Developer",
            url="https://company.atlassian.net/browse/PROJ-101",
            source_mode="live"
        )
    ]
    mock_gmail_items = [
        ConnectorItem(
            source="gmail",
            id="gmail-msg-01",
            title="Commitments for release",
            content="Priya Sharma committed to delivering auth fixes by Friday.",
            url="https://mail.google.com",
            source_mode="live"
        )
    ]

    mock_llm_response = LLMResponse(
        content="Retrieving blockers and email commitments",
        tool_calls=[
            ToolCallRequest(id="c_jira", name="jira.search_issues", arguments={"query": "blocker"}),
            ToolCallRequest(id="c_gmail", name="gmail.search_messages", arguments={"query": "commitments"})
        ]
    )
    synthesis_answer = "Authentication release is blocked by PROJ-101. Priya Sharma committed in email to delivering fixes by Friday."

    monkeypatch.setattr(agent.llm, "generate_with_tools", AsyncMock(return_value=mock_llm_response))
    monkeypatch.setattr(agent.llm, "generate_chat", AsyncMock(return_value=synthesis_answer))
    monkeypatch.setattr(agent.tool_registry.jira.connector, "search", AsyncMock(return_value=mock_jira_items))
    monkeypatch.setattr(agent.tool_registry.gmail.connector, "search", AsyncMock(return_value=mock_gmail_items))

    query = "What is blocking the authentication release, who owns each blocker, and what commitments were made in email?"
    response = await agent.run(query=query, thread_id="test_thread_01", allow_auth_gate=False)
    assert response is not None
    assert response.plan is not None
    assert len(response.plan.steps) > 0
    assert len(response.citations) > 0
    assert "PROJ-101" in response.answer or any("PROJ-101" in c.evidence_id for c in response.citations)


@pytest.mark.asyncio
async def test_agent_mutation_approval_gate(monkeypatch):
    agent = ContextMeshAgent()

    mock_response = LLMResponse(
        content="I will propose creating the Jira issue for Redis session encryption.",
        tool_calls=[
            ToolCallRequest(
                id="call_create_jira",
                name="jira.create_issue",
                arguments={"project": "SEC", "summary": "Audit Redis session encryption", "description": "Ensure encryption at rest"}
            )
        ]
    )
    monkeypatch.setattr(agent.llm, "generate_with_tools", AsyncMock(return_value=mock_response))

    query = "Create a proposed Jira task for Redis session encryption"
    response = await agent.run(query=query, thread_id="test_thread_02", can_mutate=False, allow_auth_gate=False)
    assert response.requires_approval is True
    assert response.pending_mutation is not None
    assert "Approval Required" in response.answer


@pytest.mark.asyncio
async def test_agent_jit_auth_challenge(monkeypatch):
    from security.vault import VAULT
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda x: False)

    agent = ContextMeshAgent()
    mock_response = LLMResponse(
        content="Searching Gmail for Priya's commitments",
        tool_calls=[
            ToolCallRequest(
                id="call_gmail_01",
                name="gmail.search_messages",
                arguments={"query": "Priya release commitments"}
            )
        ]
    )
    monkeypatch.setattr(agent.llm, "generate_with_tools", AsyncMock(return_value=mock_response))

    query = "What did Priya commit to in email regarding the release?"
    response = await agent.run(query=query, thread_id="test_thread_auth", allow_auth_gate=True)
    assert response is not None
    assert response.auth_required is True
    assert len(response.missing_services) > 0
    assert "Authentication Required" in response.answer
    assert response.auth_challenge is not None
    assert "query" in response.auth_challenge


def test_agent_response_coercion():
    """Verify AgentResponse coerces dicts or foreign models into QueryPlan without validation errors."""
    from agent.state import AgentResponse, QueryPlan
    from pydantic import BaseModel

    class ForeignPlan(BaseModel):
        user_intent: str = "custom_foreign_intent"
        steps: list = []

    resp = AgentResponse(
        answer="ok",
        plan=ForeignPlan(),
        citations=[{"citation_id": "c1", "evidence_id": "e1", "claim": "cl", "source_url": "#", "source_type": "jira"}]
    )
    assert isinstance(resp.plan, QueryPlan)
    assert resp.plan.user_intent == "custom_foreign_intent"
    assert len(resp.citations) == 1
    assert resp.citations[0].claim == "cl"


@pytest.mark.asyncio
async def test_agent_closed_tasks_jira_only_no_slack_auth(monkeypatch):
    """Verify that asking for closed tasks targets Jira only and never triggers Slack auth."""
    from security.vault import VAULT
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda svc: svc == "jira")

    agent = ContextMeshAgent()
    mock_llm_response = LLMResponse(
        content="Searching closed Jira tasks",
        tool_calls=[
            ToolCallRequest(
                id="call_jira_closed",
                name="jira.search_issues",
                arguments={"query": "statusCategory = Done"}
            )
        ]
    )
    synthesis_response = "All completed tasks have been verified. ATL-100 is closed."
    monkeypatch.setattr(
        agent.llm,
        "generate_with_tools",
        AsyncMock(side_effect=[
            mock_llm_response,
            LLMResponse(content="All closed tasks have been found.", tool_calls=[])
        ])
    )
    monkeypatch.setattr(agent.llm, "generate_chat", AsyncMock(return_value=synthesis_response))

    mock_items = [
        ConnectorItem(
            source="jira",
            id="ATL-100",
            title="[ATL-100] Initial setup complete",
            content="Task is Done",
            url="https://jira.com/ATL-100",
            source_mode="live",
            metadata={"status": "Done"}
        )
    ]
    monkeypatch.setattr(agent.tool_registry.jira.connector, "search", AsyncMock(return_value=mock_items))

    query = "what are my closed tasks"
    response = await agent.run(query=query, thread_id="test_closed_tasks_thread", allow_auth_gate=True)
    assert response is not None
    assert response.auth_required is False
    assert response.required_services == ["jira"]
    assert "slack" not in response.required_services
    tools = [s.tool for s in response.plan.steps]
    assert tools == ["jira.search_issues"]
    assert len(response.citations) > 0
    assert any("ATL-100" in c.claim or "ATL-100" in c.evidence_id for c in response.citations)


@pytest.mark.asyncio
async def test_agent_skip_unauthenticated_pruning(monkeypatch):
    """Verify skip_unauthenticated=True prunes unauthenticated services and proceeds with connected ones."""
    from security.vault import VAULT
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda svc: svc == "jira")

    agent = ContextMeshAgent()
    mock_llm_response = LLMResponse(
        content="Searching Jira and Slack for open tasks",
        tool_calls=[
            ToolCallRequest(id="c1", name="jira.search_issues", arguments={"query": "status != Done"}),
            ToolCallRequest(id="c2", name="slack.search_messages", arguments={"query": "open tasks"})
        ]
    )
    monkeypatch.setattr(agent.llm, "generate_with_tools", AsyncMock(return_value=mock_llm_response))

    mock_items = [
        ConnectorItem(
            source="jira",
            id="PROJ-200",
            title="[PROJ-200] Active Task",
            content="In progress",
            url="https://jira.com",
            source_mode="live"
        )
    ]
    monkeypatch.setattr(agent.tool_registry.jira.connector, "search", AsyncMock(return_value=mock_items))

    query = "what are my opened tasks"
    response = await agent.run(query=query, thread_id="test_skip_auth_thread", allow_auth_gate=True, skip_unauthenticated=True)
    assert response is not None
    assert response.auth_required is False
    assert response.required_services == ["jira"]
    assert response.skipped_services == ["slack"]
    assert len(response.citations) > 0


@pytest.mark.asyncio
async def test_agent_auth_challenge_contains_connected_services(monkeypatch):
    """Verify auth challenge includes connected_services so UI knows what is already connected."""
    from security.vault import VAULT
    monkeypatch.setattr(VAULT, "is_service_authenticated", lambda svc: svc == "jira")

    agent = ContextMeshAgent()
    mock_llm_response = LLMResponse(
        content="Searching Jira and Slack",
        tool_calls=[
            ToolCallRequest(id="c1", name="jira.search_issues", arguments={"query": "tasks"}),
            ToolCallRequest(id="c2", name="slack.search_messages", arguments={"query": "tasks"})
        ]
    )
    monkeypatch.setattr(agent.llm, "generate_with_tools", AsyncMock(return_value=mock_llm_response))

    query = "what are my opened tasks"
    response = await agent.run(query=query, thread_id="test_challenge_thread", allow_auth_gate=True, skip_unauthenticated=False)
    assert response is not None
    assert response.auth_required is True
    assert response.auth_challenge is not None
    assert "jira" in response.auth_challenge.get("connected_services", [])
    assert "slack" in response.auth_challenge.get("missing_services", [])
