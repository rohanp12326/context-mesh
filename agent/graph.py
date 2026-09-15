"""ContextMesh Agent Graph - Orchestrates autonomous ReAct retrieval, parallel tool execution, memory, and synthesis."""

import asyncio
import inspect
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional
from agent.state import AgentResponse, AgentState, PlanStep, QueryPlan, ReActStep
from agent.planner import QueryPlanner
from agent.synthesis import AnswerSynthesizer
from agent.policies import PolicyEngine
from agent.router import AppRouter
from connectors.base import ConnectorItem, PermissionScope
from mcp_servers.base import MCPToolCall
from mcp_servers.registry import MCPToolRegistry
from memory.short_term import ShortTermMemoryStore
from memory.long_term import LongTermMemoryStore
from memory.promotion import MemoryPromotionEngine
from retrieval.normalization import normalize_evidence_list
from retrieval.freshness import detect_contradictions, evaluate_freshness
from retrieval.ranking import rank_evidence, check_sufficiency
from observability.tracing import GLOBAL_TRACER, RequestTrace
from observability.logging import get_logger

logger = get_logger("agent.graph")


class ContextMeshAgent:
    """Enterprise AI Intelligence Agent for autonomous cross-system query answering via ReAct."""

    def __init__(
        self,
        tool_registry: Optional[MCPToolRegistry] = None,
        short_term_memory: Optional[ShortTermMemoryStore] = None,
        long_term_memory: Optional[LongTermMemoryStore] = None,
        llm_client: Optional[Any] = None,
    ):
        self.tool_registry = tool_registry or MCPToolRegistry()
        self.short_term = short_term_memory or ShortTermMemoryStore()
        self.long_term = long_term_memory or LongTermMemoryStore()
        self.promotion_engine = MemoryPromotionEngine(self.long_term)
        self.planner = QueryPlanner(llm_client=llm_client, memory_store=self.long_term)
        self.synthesizer = AnswerSynthesizer(llm_client=self.planner.llm)
        self.llm = self.planner.llm

    async def run(
        self,
        query: str,
        user_id: str = "default_user",
        thread_id: str = "default_thread",
        can_mutate: bool = False,
        allow_auth_gate: bool = True,
        require_live: bool = False,
        skip_unauthenticated: bool = False,
        max_iterations: int = 3,
        on_step: Optional[Callable[[Dict[str, Any]], Any]] = None,
        **kwargs
    ) -> AgentResponse:
        """Run the autonomous ReAct retrieval and synthesis loop for a user query."""
        logger.info(
            f"AgentGraph.run: query='{query}', user='{user_id}', "
            f"thread='{thread_id}', can_mutate={can_mutate}, "
            f"allow_auth_gate={allow_auth_gate}, skip_unauthenticated={skip_unauthenticated}"
        )
        trace: RequestTrace = GLOBAL_TRACER.start_trace("agent_request", user_id=user_id, thread_id=thread_id)
        root_span = trace.root_span
        react_steps: List[ReActStep] = []

        async def notify_step(event: Dict[str, Any]):
            if on_step:
                try:
                    if inspect.iscoroutinefunction(on_step):
                        await on_step(event)
                    else:
                        on_step(event)
                except Exception as ex:
                    logger.debug(f"on_step callback error: {ex}")

        # 1. Thread state & Working Memory Setup
        mem_span = GLOBAL_TRACER.add_span(root_span, "memory_retrieval")
        thread_state = self.short_term.get_or_create(thread_id, user_id=user_id)
        prior_history = [
            {"role": m["role"], "content": m["content"]}
            for m in thread_state.messages
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        self.short_term.append_message(thread_id, "user", query)
        memory_context = self.planner.build_memory_context(query)
        resolved_entities = self.planner.resolve_entities(query)
        mem_span.finish()

        # 2. Zero-Tool Fast-Path Check
        if AppRouter.is_zero_tool_query(query):
            zero_thought = "Query classified as zero-tool general knowledge. Synthesizing direct response without querying enterprise systems."
            logger.info("Query classified as zero-tool/direct-answer. Bypassing tool execution and auth gates.")
            logger.info(f"🧠 [THOUGHT - ZeroTool]: {zero_thought}")
            await notify_step({"type": "thought", "iteration": 0, "content": zero_thought})

            gen_span = GLOBAL_TRACER.add_span(root_span, "direct_answer_generation")
            response = await self.synthesizer.synthesize_direct(query=query, trace_id=trace.trace_id, history=prior_history[-10:])
            response.plan = QueryPlan(
                user_intent="direct_answer",
                entities=resolved_entities,
                steps=[],
                risk_level="low",
                requires_approval=False
            )
            response.required_services = []
            response.react_steps = [
                ReActStep(
                    iteration=0,
                    thought=zero_thought,
                    tool_calls=[],
                    observations=[],
                    duration_ms=0.0
                )
            ]
            gen_span.finish()

            self.short_term.append_message(thread_id, "assistant", response.answer)
            GLOBAL_TRACER.finish_trace(trace.trace_id)
            await notify_step({"type": "complete", "react_steps": len(response.react_steps)})
            logger.info(f"ContextMeshAgent.run() completed direct answer. Trace: {trace.trace_id}")
            return response

        # 3. Prepare Tools and Prompt for ReAct
        tool_defs = self.tool_registry.get_all_tool_definitions()
        formatted_tool_descs = "\n".join(f"- {t.name}: {t.description}" for t in tool_defs)

        react_system_prompt = (
            "You are ContextMesh, an autonomous enterprise intelligence assistant.\n"
            "You answer questions by searching enterprise tools: Jira, Slack, Gmail, and Web Search.\n\n"
            f"User Context:\n- Current Requester ID: {user_id}\n\n"
            f"Organizational Memory:\n{memory_context}\n\n"
            f"Available Tools:\n{formatted_tool_descs}\n\n"
            "Autonomous ReAct Protocol:\n"
            "1. Reason before acting: Formulate your thought explaining what data you need and why you choose specific tools.\n"
            "2. ALWAYS invoke tools to retrieve factual data before answering enterprise questions.\n"
            "3. When searching Slack, you can use query syntax like 'from:<username>' to find messages sent by a specific person, or keywords to find content.\n"
            "4. When searching Gmail, use Gmail search operators. For 'my latest/recent emails' use 'in:inbox newer_than:7d' (received mail); use 'in:sent' for mail you sent. Never use 'from:me' to mean your inbox, and never pass vague filler like 'latest recent' as free text.\n"
            "5. Use the web search tool for current events, breaking news, live data, or anything that may have changed recently; prefer it over guessing from memory.\n"
            "6. Pay close attention to message authors/senders versus recipients: verify who actually sent a message rather than assuming a message addressing someone was sent by them.\n"
            "7. You can call multiple tools in parallel.\n"
            "8. After observing results, inspect if evidence is sufficient. If yes, synthesize the answer with citations.\n"
            "9. For mutations (create_issue, post_message), formulate the tool call and stop for human approval.\n"
            "10. Point out conflicting information and note recent versus stale updates.\n"
            "11. Prior conversation history for this thread is provided below. Use it to resolve "
            "references to earlier topics, follow-up questions, and questions about the conversation itself "
            "(e.g., 'what have we discussed so far?'), rather than treating the current query as isolated.\n"
        )

        messages: List[Dict[str, Any]] = [{"role": "system", "content": react_system_prompt}]
        recent_history = prior_history[-10:]
        for h in recent_history:
            if messages and messages[-1]["role"] == h["role"]:
                messages[-1]["content"] += "\n" + h["content"]
            else:
                messages.append(dict(h))
        messages.append({"role": "user", "content": query})

        all_raw_items: List[ConnectorItem] = []
        executed_plan_steps: List[PlanStep] = []
        tool_failures: List[Dict[str, Any]] = []
        required_services: List[str] = []
        skipped_services: List[str] = []
        llm_final_answer: Optional[str] = None

        scope = PermissionScope(
            user_id=user_id,
            allowed_scopes=["read:jira", "read:slack", "read:gmail", "write:jira" if can_mutate else "", "write:slack" if can_mutate else ""],
            can_mutate=can_mutate
        )

        # 4. Autonomous ReAct Loop
        for iteration in range(max_iterations):
            iter_start_t = time.time()
            logger.info(f"🔄 [ReAct Loop] --- Starting Iteration {iteration + 1}/{max_iterations} ---")
            await notify_step({"type": "iteration_start", "iteration": iteration + 1, "max_iterations": max_iterations})

            llm_response = await self.llm.generate_with_tools(
                messages=messages,
                tools=tool_defs,
                temperature=0.1
            )

            # Case A: LLM decided to invoke tool(s)
            if llm_response.tool_calls:
                initial_calls = llm_response.tool_calls
                thought = llm_response.thought or llm_response.content
                if not thought:
                    tool_names_str = ", ".join(f"`{tc.name}`" for tc in initial_calls)
                    if iteration == 0:
                        thought = f"Analyzing inquiry for required enterprise context. Invoking {tool_names_str} to retrieve authoritative data."
                    else:
                        thought = f"Evaluating observations from previous iteration ({len(all_raw_items)} items retrieved so far). Invoking {tool_names_str} to expand context."

                logger.info(f"🧠 [THOUGHT - Iteration {iteration + 1}]: {thought}")
                await notify_step({"type": "thought", "iteration": iteration + 1, "content": thought})

                for tc in initial_calls:
                    args_str = json.dumps(tc.arguments, ensure_ascii=False)
                    logger.info(f"🛠️ [ACTION - Iteration {iteration + 1}]: Calling Tool '{tc.name}' with parameters: {args_str}")
                    await notify_step({
                        "type": "action",
                        "iteration": iteration + 1,
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "call_id": tc.id
                    })

                # Determine services needed
                batch_services = list(dict.fromkeys(
                    tc.name.split(".")[0] for tc in initial_calls if "." in tc.name
                ))
                for bs in batch_services:
                    if bs not in required_services:
                        required_services.append(bs)

                # --- Just-In-Time Auth Gate Check ---
                if allow_auth_gate or require_live:
                    from security.vault import VAULT
                    missing_services = VAULT.get_missing_services(required_services)
                    connected_services = [s for s in required_services if s not in missing_services]

                    if skip_unauthenticated and missing_services:
                        logger.info(f"skip_unauthenticated=True: Pruning tool calls for unauthenticated {missing_services}")
                        skipped_services = list(missing_services)
                        initial_calls = [tc for tc in initial_calls if tc.name.split(".")[0] not in missing_services]
                        required_services = [s for s in required_services if s not in missing_services]

                        if not initial_calls:
                            GLOBAL_TRACER.finish_trace(trace.trace_id)
                            services_str = ", ".join(s.upper() for s in skipped_services)
                            auth_skip_step = ReActStep(
                                iteration=iteration + 1,
                                thought=thought,
                                tool_calls=[{"tool": tc.name, "arguments": tc.arguments, "call_id": tc.id} for tc in llm_response.tool_calls],
                                observations=[{"tool": s, "success": False, "error": "Unauthenticated service skipped", "summary": f"Skipped unauthenticated service: {s}"} for s in skipped_services],
                                duration_ms=round((time.time() - iter_start_t) * 1000, 2)
                            )
                            react_steps.append(auth_skip_step)
                            await notify_step({"type": "complete", "react_steps": len(react_steps)})
                            return AgentResponse(
                                answer=(
                                    f"⚠️ All required services ({services_str}) were skipped because they lack live authentication. "
                                    "Please connect your account(s) to query your real enterprise data."
                                ),
                                citations=[],
                                confidence=0.0,
                                plan=QueryPlan(user_intent="auth_skipped", steps=[], risk_level="low"),
                                skipped_services=skipped_services,
                                required_services=[],
                                react_steps=react_steps
                            )

                    elif missing_services:
                        logger.info(f"Query requires services {required_services}, but {missing_services} lack live authentication. Halting for JIT auth.")
                        GLOBAL_TRACER.finish_trace(trace.trace_id)
                        services_str = ", ".join(s.upper() for s in missing_services)
                        interim_plan = QueryPlan(
                            user_intent="cross_tool_inquiry" if len(required_services) > 1 else "app_specific_inquiry",
                            entities=resolved_entities,
                            steps=[
                                PlanStep(
                                    id=f"step_{idx+1}",
                                    tool=tc.name,
                                    query=str(tc.arguments.get("query", "")),
                                    purpose=f"Search {tc.name}",
                                    arguments=tc.arguments
                                )
                                for idx, tc in enumerate(initial_calls)
                            ],
                            risk_level="low",
                            requires_approval=False
                        )
                        auth_gate_step = ReActStep(
                            iteration=iteration + 1,
                            thought=thought,
                            tool_calls=[{"tool": tc.name, "arguments": tc.arguments, "call_id": tc.id} for tc in initial_calls],
                            observations=[{"tool": s, "success": False, "error": "Auth required", "summary": f"Service requires live authentication: {s}"} for s in missing_services],
                            duration_ms=round((time.time() - iter_start_t) * 1000, 2)
                        )
                        react_steps.append(auth_gate_step)
                        await notify_step({"type": "complete", "react_steps": len(react_steps)})
                        return AgentResponse(
                            answer=(
                                f"🔐 **Authentication Required**: This query needs data from **{services_str}**. "
                                "Connect your account(s) below to fetch your real enterprise data."
                            ),
                            citations=[],
                            confidence=1.0,
                            plan=interim_plan,
                            contradictions=[],
                            trace_id=trace.trace_id,
                            requires_approval=False,
                            auth_required=True,
                            missing_services=missing_services,
                            required_services=required_services,
                            skipped_services=skipped_services,
                            auth_challenge={
                                "query": query,
                                "required_services": required_services,
                                "missing_services": missing_services,
                                "connected_services": connected_services,
                                "plan": interim_plan.model_dump()
                            },
                            react_steps=react_steps
                        )

                # --- Mutation Approval Gate Check ---
                mutation_calls = [
                    tc for tc in initial_calls
                    if tc.name in ("jira.create_issue", "slack.post_message")
                    or getattr(self.tool_registry.get_tool_definition(tc.name), "requires_approval", False)
                ]

                if mutation_calls and not can_mutate:
                    logger.warning("ReAct loop encountered mutation call without approval. Halting for human approval.")
                    GLOBAL_TRACER.finish_trace(trace.trace_id)
                    mut_call = mutation_calls[0]
                    pending_action = {
                        "action": mut_call.name,
                        "params": dict(mut_call.arguments)
                    }
                    self.short_term.set_pending_approval(thread_id, pending_action)

                    interim_plan = QueryPlan(
                        user_intent="mutation_operation",
                        entities=resolved_entities,
                        steps=[
                            PlanStep(
                                id="step_mut_1",
                                tool=mut_call.name,
                                query=str(mut_call.arguments.get("summary", query[:60])),
                                purpose=f"Execute {mut_call.name}",
                                arguments=mut_call.arguments
                            )
                        ],
                        risk_level="high",
                        requires_approval=True
                    )

                    target_svc = mut_call.name.split(".")[0].title()
                    mut_gate_step = ReActStep(
                        iteration=iteration + 1,
                        thought=thought,
                        tool_calls=[{"tool": tc.name, "arguments": tc.arguments, "call_id": tc.id} for tc in initial_calls],
                        observations=[{"tool": mut_call.name, "success": False, "error": "Approval required", "summary": f"Mutation requires human confirmation before execution"}],
                        duration_ms=round((time.time() - iter_start_t) * 1000, 2)
                    )
                    react_steps.append(mut_gate_step)
                    await notify_step({"type": "complete", "react_steps": len(react_steps)})
                    return AgentResponse(
                        answer=(
                            f"⚠️ **Approval Required**: This operation creates a mutation in **{target_svc}**. "
                            "In accordance with security policies, mutations require human confirmation before execution."
                        ),
                        citations=[],
                        confidence=1.0,
                        plan=interim_plan,
                        contradictions=[],
                        trace_id=trace.trace_id,
                        requires_approval=True,
                        pending_mutation=pending_action,
                        required_services=required_services,
                        react_steps=react_steps
                    )

                # --- Tool Execution ---
                tools_span = GLOBAL_TRACER.add_span(root_span, "tool_execution", {"iteration": iteration, "count": len(initial_calls)})
                mcp_calls = [
                    MCPToolCall(call_id=tc.id or f"step_{len(executed_plan_steps)+idx+1}", tool_name=tc.name, arguments=tc.arguments)
                    for idx, tc in enumerate(initial_calls)
                ]
                results = await self.tool_registry.execute_parallel(mcp_calls, scope=scope)
                tools_span.finish()

                # Process observations
                step_observations: List[Dict[str, Any]] = []
                for tc, res in zip(initial_calls, results):
                    step_id = tc.id or f"step_{len(executed_plan_steps)+1}"
                    executed_plan_steps.append(
                        PlanStep(
                            id=step_id,
                            tool=tc.name,
                            query=str(tc.arguments.get("query", "")),
                            purpose=f"Retrieve data via {tc.name}",
                            arguments=tc.arguments
                        )
                    )

                    if res.success and res.data is not None:
                        step_items: List[ConnectorItem] = []
                        if isinstance(res.data, list):
                            for d in res.data:
                                step_items.append(ConnectorItem(**d) if isinstance(d, dict) else d)
                        elif isinstance(res.data, dict) and "id" in res.data:
                            step_items.append(ConnectorItem(**res.data))

                        all_raw_items.extend(step_items)

                        # Format concise observation for LLM context
                        if step_items:
                            obs_lines = [f"Result for {tc.name} ({len(step_items)} items):"]
                            for it in step_items[:8]:
                                author_str = f"Author: {it.author} | " if it.author else ""
                                channel_str = f"Channel: #{it.metadata.get('channel')} | " if (it.metadata and it.metadata.get("channel")) else ""
                                date_str = f"Date: {it.updated_at or it.created_at} | " if (it.updated_at or it.created_at) else ""
                                obs_lines.append(f"- [{it.source}:{it.id}] ({author_str}{channel_str}{date_str}Title: {it.title}) {it.content[:150]}")
                            obs_text = "\n".join(obs_lines)
                            obs_summary = ", ".join(f"[{it.id}] {it.title} ({it.author})" if it.author else f"[{it.id}] {it.title}" for it in step_items[:3])
                        else:
                            obs_text = f"Result for {tc.name}: 0 items found."
                            obs_summary = "0 items found."

                        logger.info(f"👁️ [OBSERVATION - Iteration {iteration + 1}]: Tool '{tc.name}' returned {len(step_items)} item(s) -> {obs_summary}")
                        await notify_step({
                            "type": "observation",
                            "iteration": iteration + 1,
                            "tool": tc.name,
                            "success": True,
                            "count": len(step_items),
                            "summary": obs_summary,
                            "items": [{"id": it.id, "title": it.title, "source": it.source, "author": it.author} for it in step_items[:3]]
                        })
                        step_observations.append({
                            "tool": tc.name,
                            "success": True,
                            "items_count": len(step_items),
                            "summary": obs_summary,
                            "error": None
                        })

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": obs_text
                        })
                    else:
                        err_msg = res.error or "Unknown tool error"
                        tool_failures.append({"tool": tc.name, "error": err_msg})
                        logger.warning(f"👁️ [OBSERVATION - Iteration {iteration + 1}]: Tool '{tc.name}' FAILED -> {err_msg}")
                        await notify_step({
                            "type": "observation",
                            "iteration": iteration + 1,
                            "tool": tc.name,
                            "success": False,
                            "count": 0,
                            "summary": f"Error: {err_msg}",
                            "error": err_msg
                        })
                        step_observations.append({
                            "tool": tc.name,
                            "success": False,
                            "items_count": 0,
                            "summary": f"Error: {err_msg}",
                            "error": err_msg
                        })
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"Tool {tc.name} returned error: {err_msg}"
                        })

                iter_duration = round((time.time() - iter_start_t) * 1000, 2)
                react_steps.append(
                    ReActStep(
                        iteration=iteration + 1,
                        thought=thought,
                        tool_calls=[{"tool": tc.name, "arguments": tc.arguments, "call_id": tc.id} for tc in initial_calls],
                        observations=step_observations,
                        duration_ms=iter_duration
                    )
                )

            # Case B: LLM provided final answer content (no further tools required)
            elif llm_response.content:
                llm_final_answer = llm_response.content
                synthesis_thought = llm_response.thought or "Sufficient evidence collected across enterprise systems. Concluding search and synthesizing final verified answer."
                logger.info(f"🧠 [THOUGHT - Iteration {iteration + 1}]: {synthesis_thought}")
                logger.info("🎯 [SYNTHESIS]: LLM concluded ReAct search loop. Proceeding to final synthesis.")
                await notify_step({"type": "thought", "iteration": iteration + 1, "content": synthesis_thought})
                iter_duration = round((time.time() - iter_start_t) * 1000, 2)
                react_steps.append(
                    ReActStep(
                        iteration=iteration + 1,
                        thought=synthesis_thought,
                        tool_calls=[],
                        observations=[],
                        duration_ms=iter_duration
                    )
                )
                break

        # 5. Evidence Normalization & Freshness
        norm_span = GLOBAL_TRACER.add_span(root_span, "evidence_normalization", {"raw_items_count": len(all_raw_items)})
        normalized_evidence = normalize_evidence_list(all_raw_items)
        for ev in normalized_evidence:
            evaluate_freshness(ev)
            ev.content = PolicyEngine.sanitize_untrusted_text(ev.content)

        # 6. Contradictions & Relevance Ranking
        contradictions = detect_contradictions(normalized_evidence)
        ranked_evidence = rank_evidence(query, normalized_evidence, top_k=15)
        norm_span.finish()

        # 7. Final Response Synthesis
        logger.info(f"🎯 [SYNTHESIS]: Finalizing answer with {len(ranked_evidence)} evidence item(s) across {len(react_steps)} ReAct iteration(s).")
        await notify_step({
            "type": "synthesis",
            "status": "synthesizing",
            "evidence_count": len(ranked_evidence),
            "iterations": len(react_steps)
        })

        gen_span = GLOBAL_TRACER.add_span(root_span, "answer_generation", {"evidence_count": len(ranked_evidence)})
        final_plan = QueryPlan(
            user_intent="cross_tool_inquiry" if len(required_services) > 1 else "app_specific_inquiry",
            entities=resolved_entities,
            steps=executed_plan_steps,
            risk_level="low",
            requires_approval=False
        )

        response = await self.synthesizer.synthesize(
            query=query,
            plan=final_plan,
            evidence=ranked_evidence,
            contradictions=contradictions,
            trace_id=trace.trace_id,
            tool_failures=tool_failures,
            draft_answer=llm_final_answer,
            history=recent_history
        )
        response.required_services = required_services
        response.skipped_services = skipped_services
        response.react_steps = react_steps
        gen_span.finish()

        # 8. Dynamic Candidate Memory Promotion
        mem_update_span = GLOBAL_TRACER.add_span(root_span, "memory_update")
        if "alias" in query.lower():
            alias_match = re.search(r"alias\s+(?:for\s+)?([a-zA-Z0-9_-]+)\s*(?:as|is|=|to)\s*([a-zA-Z0-9_-]+)", query, re.I)
            if alias_match:
                alias_name, proj_key = alias_match.groups()
                self.promotion_engine.promote_alias_candidate(alias=alias_name, jira_project=proj_key, source_ref=thread_id)
            elif "set project alias" in query.lower() or "atlas" in query.lower():
                self.promotion_engine.promote_alias_candidate(alias="Atlas", jira_project="ATL", source_ref=thread_id)

        self.short_term.append_message(thread_id, "assistant", response.answer)
        mem_update_span.finish()

        await notify_step({"type": "complete", "react_steps": len(react_steps)})
        GLOBAL_TRACER.finish_trace(trace.trace_id)
        logger.info(f"ContextMeshAgent.run() completed. Trace: {trace.trace_id}")
        return response
