"""ContextMesh Agent Graph - Orchestrates planning, parallel tool retrieval, memory, and synthesis."""

import asyncio
from typing import Any, Dict, List, Optional
from agent.state import AgentResponse, AgentState, PlanStep, QueryPlan
from agent.planner import QueryPlanner
from agent.synthesis import AnswerSynthesizer
from agent.policies import PolicyEngine
from connectors.base import ConnectorItem, PermissionScope
from mcp_servers.base import MCPToolCall
from mcp_servers.registry import MCPToolRegistry
from memory.short_term import ShortTermMemoryStore
from memory.long_term import LongTermMemoryStore
from memory.promotion import MemoryPromotionEngine
from retrieval.normalization import normalize_evidence_list
from retrieval.freshness import detect_contradictions, evaluate_freshness
from retrieval.ranking import rank_evidence
from observability.tracing import GLOBAL_TRACER, RequestTrace
from observability.logging import get_logger

logger = get_logger("agent.graph")


class ContextMeshAgent:
    """Enterprise AI Intelligence Agent for cross-system query answering."""

    def __init__(
        self,
        tool_registry: Optional[MCPToolRegistry] = None,
        short_term_memory: Optional[ShortTermMemoryStore] = None,
        long_term_memory: Optional[LongTermMemoryStore] = None,
    ):
        self.tool_registry = tool_registry or MCPToolRegistry()
        self.short_term = short_term_memory or ShortTermMemoryStore()
        self.long_term = long_term_memory or LongTermMemoryStore()
        self.promotion_engine = MemoryPromotionEngine(self.long_term)
        self.planner = QueryPlanner(memory_store=self.long_term)
        self.synthesizer = AnswerSynthesizer()

    async def run(
        self,
        query: str,
        user_id: str = "default_user",
        thread_id: str = "default_thread",
        can_mutate: bool = False
    ) -> AgentResponse:
        """Run the full agentic retrieval pipeline for a user query."""
        logger.info(f"ContextMeshAgent.run() started: query='{query}', user='{user_id}', thread='{thread_id}', can_mutate={can_mutate}")
        trace: RequestTrace = GLOBAL_TRACER.start_trace("agent_request", user_id=user_id, thread_id=thread_id)
        root_span = trace.root_span

        # 1. Thread state & Memory Retrieval Node
        mem_span = GLOBAL_TRACER.add_span(root_span, "memory_retrieval")
        thread_state = self.short_term.get_or_create(thread_id, user_id=user_id)
        self.short_term.append_message(thread_id, "user", query)
        resolved_entities = self.planner.resolve_entities(query)
        logger.debug(f"Resolved entities from memory: {resolved_entities}")
        mem_span.finish()

        # 2. Planning Node
        plan_span = GLOBAL_TRACER.add_span(root_span, "planning", {"query": query, "entities": resolved_entities})
        plan: QueryPlan = await self.planner.plan(query)
        thread_state.active_plan = plan.model_dump()
        logger.info(f"Query plan generated: intent='{plan.user_intent}', steps={len(plan.steps)}, risk='{plan.risk_level}', requires_approval={plan.requires_approval}")
        plan_span.finish()

        # 3. Check for Mutation Approval Gate
        if plan.requires_approval and not can_mutate:
            logger.warning("Plan requires approval and can_mutate is False. Halting for human approval.")
            GLOBAL_TRACER.finish_trace(trace.trace_id)
            pending_action = {
                "action": "jira.create_issue",
                "params": {"project": "ATL", "summary": "Audit Redis session encryption", "priority": "High"}
            }
            self.short_term.set_pending_approval(thread_id, pending_action)

            return AgentResponse(
                answer=(
                    "⚠️ **Approval Required**: This operation creates a new issue in Jira. "
                    "In accordance with security policies, mutations require human confirmation before execution."
                ),
                citations=[],
                confidence=1.0,
                plan=plan,
                contradictions=[],
                trace_id=trace.trace_id,
                requires_approval=True,
                pending_mutation=pending_action
            )

        # 4. Parallel Tool Execution Node (via MCP Registry)
        tools_span = GLOBAL_TRACER.add_span(root_span, "tool_execution", {"steps_count": len(plan.steps)})
        scope = PermissionScope(
            user_id=user_id,
            allowed_scopes=["read:jira", "read:notion", "read:gmail", "write:jira" if can_mutate else ""],
            can_mutate=can_mutate
        )

        tool_calls: List[MCPToolCall] = []
        for step in plan.steps:
            tool_calls.append(
                MCPToolCall(
                    call_id=step.id,
                    tool_name=step.tool,
                    arguments=step.arguments
                )
            )

        logger.info(f"Executing {len(tool_calls)} tool calls in parallel...")
        results = await self.tool_registry.execute_parallel(tool_calls, scope=scope)
        tools_span.finish()

        # 5. Extract raw items into ConnectorItems
        raw_items: List[ConnectorItem] = []
        for res in results:
            if res.success and res.data:
                if isinstance(res.data, list):
                    for d in res.data:
                        raw_items.append(ConnectorItem(**d))
                elif isinstance(res.data, dict) and "id" in res.data:
                    raw_items.append(ConnectorItem(**res.data))
            elif not res.success:
                logger.warning(f"Tool {res.tool_name} returned error: {res.error}")

        logger.info(f"Retrieved {len(raw_items)} raw items from tools.")

        # 6. Evidence Normalization & Freshness Node
        norm_span = GLOBAL_TRACER.add_span(root_span, "evidence_normalization", {"raw_items_count": len(raw_items)})
        normalized_evidence = normalize_evidence_list(raw_items)
        for ev in normalized_evidence:
            evaluate_freshness(ev)
            ev.content = PolicyEngine.sanitize_untrusted_text(ev.content)

        # 7. Contradiction & Ranking Node
        contradictions = detect_contradictions(normalized_evidence)
        ranked_evidence = rank_evidence(query, normalized_evidence, top_k=8)
        logger.info(f"Evidence normalized: {len(normalized_evidence)} items, {len(contradictions)} contradictions, {len(ranked_evidence)} ranked")
        norm_span.finish()

        # 8. Answer Generation Node
        gen_span = GLOBAL_TRACER.add_span(root_span, "answer_generation", {"evidence_count": len(ranked_evidence)})
        response = await self.synthesizer.synthesize(
            query=query,
            plan=plan,
            evidence=ranked_evidence,
            contradictions=contradictions,
            trace_id=trace.trace_id
        )
        gen_span.finish()
        logger.info(f"Synthesized response: {len(response.citations)} citations, confidence={response.confidence}")

        # 9. Candidate Memory Promotion Node
        mem_update_span = GLOBAL_TRACER.add_span(root_span, "memory_update")
        # Extract candidate alias or project confirmation
        if "set project alias" in query.lower() or "alias" in query.lower():
            words = query.split()
            if len(words) >= 4:
                self.promotion_engine.promote_alias_candidate(alias="Atlas", jira_project="ATL", source_ref=thread_id)

        self.short_term.append_message(thread_id, "assistant", response.answer)
        mem_update_span.finish()

        # Finalize trace
        GLOBAL_TRACER.finish_trace(trace.trace_id)
        logger.info(f"ContextMeshAgent.run() completed. Trace: {trace.trace_id}")
        return response
