"""Tests verifying autonomous ReAct agent behavior, typo tolerance, and failure reporting."""

import pytest
from agent.graph import ContextMeshAgent
from memory.long_term import LongTermMemoryStore, MemoryRecord


@pytest.mark.asyncio
async def test_react_loop_typo_query_urgent_tasks():
    """Verify that typo-laden query 'What are my most urgernt tasks' works autonomously."""
    agent = ContextMeshAgent()
    response = await agent.run("What are my most urgernt tasks", thread_id="test_typo_thread")

    assert response is not None
    assert response.plan is not None
    assert len(response.plan.steps) > 0
    # Should use jira tool
    assert any("jira" in s.tool for s in response.plan.steps)
    assert response.confidence > 0.0


@pytest.mark.asyncio
async def test_react_loop_mutation_approval_gate(monkeypatch):
    """Verify ReAct loop halts immediately upon encountering mutation tool call."""
    from unittest.mock import AsyncMock
    from agent.llm_types import LLMResponse, ToolCallRequest

    agent = ContextMeshAgent()
    mock_llm_response = LLMResponse(
        content="Proposing creation of Jira issue",
        tool_calls=[
            ToolCallRequest(
                id="call_create",
                name="jira.create_issue",
                arguments={"project": "ATL", "summary": "Audit Redis session encryption"}
            )
        ]
    )
    monkeypatch.setattr(agent.llm, "generate_with_tools", AsyncMock(return_value=mock_llm_response))

    query = "Create a proposed Jira task for Redis session encryption"
    response = await agent.run(query=query, thread_id="test_react_mutation", can_mutate=False, allow_auth_gate=False)
    assert response.requires_approval is True
    assert response.pending_mutation is not None
    assert "Approval Required" in response.answer


@pytest.mark.asyncio
async def test_dynamic_memory_entity_resolution():
    """Verify agent dynamically resolves new organizational memory entities without hardcoded lists."""
    mem_store = LongTermMemoryStore()
    custom_record = MemoryRecord(
        namespace=["global", "projects"],
        type="project_alias",
        content={
            "alias": "Nebula",
            "jira_project": "NEB",
            "slack_channel": "proj-nebula-dev"
        }
    )
    mem_store.add_record(custom_record)

    agent = ContextMeshAgent(long_term_memory=mem_store)
    context = agent.planner.build_memory_context("What is the status of project Nebula?")
    assert "Nebula" in context
    assert "NEB" in context


@pytest.mark.asyncio
async def test_react_zero_tool_direct_answer():
    """Verify zero tool queries bypass tool calls and generate direct answers."""
    agent = ContextMeshAgent()
    response = await agent.run("who is the president of america", thread_id="test_zero_react")

    assert response is not None
    assert response.plan is not None
    assert len(response.plan.steps) == 0
    assert len(response.citations) == 0
    assert "President of the United States" in response.answer or "Biden" in response.answer


@pytest.mark.asyncio
async def test_react_steps_contain_thought_action_observation():
    """Verify AgentResponse populates detailed ReActStep objects with thoughts, actions, and observations."""
    agent = ContextMeshAgent()
    response = await agent.run("What are my most urgent tasks", thread_id="test_react_steps_thread")

    assert response is not None
    assert len(response.react_steps) > 0
    first_step = response.react_steps[0]
    assert first_step.iteration >= 1
    assert first_step.thought and len(first_step.thought) > 10
    assert len(first_step.tool_calls) > 0
    assert any("jira" in tc["tool"] for tc in first_step.tool_calls)
    assert len(first_step.observations) > 0
    obs = first_step.observations[0]
    assert obs.get("success") is True
    assert obs.get("summary") is not None


@pytest.mark.asyncio
async def test_react_on_step_streaming_callback():
    """Verify on_step callback receives real-time thought, action, observation, and synthesis events."""
    agent = ContextMeshAgent()
    events = []

    def callback(evt):
        events.append(evt)

    response = await agent.run(
        "What are my most urgent tasks",
        thread_id="test_streaming_callback",
        on_step=callback
    )

    assert response is not None
    assert len(events) > 0

    event_types = [e.get("type") for e in events]
    assert "iteration_start" in event_types
    assert "thought" in event_types
    assert "action" in event_types
    assert "observation" in event_types
    assert "synthesis" in event_types
    assert "complete" in event_types

    # Verify thought content
    thought_evts = [e for e in events if e.get("type") == "thought"]
    assert any("jira" in e.get("content", "").lower() or "analyzing" in e.get("content", "").lower() or "task" in e.get("content", "").lower() for e in thought_evts)

    # Verify action content
    action_evts = [e for e in events if e.get("type") == "action"]
    assert any("jira.search_issues" in e.get("tool", "") for e in action_evts)
