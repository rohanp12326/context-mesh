"""Evidence-backed answer synthesizer producing cited, grounded responses."""

import json
from typing import List, Optional
from agent.llm import ZAIClient
from agent.state import AgentResponse, Citation, QueryPlan
from retrieval.normalization import Evidence
from retrieval.freshness import Contradiction


SYNTHESIS_SYSTEM_PROMPT = """You are ContextMesh, an AI engineering intelligence assistant.
Answer the user's question accurately using ONLY the provided evidence.

Requirements:
1. Ground every substantive claim in one or more evidence items using bracket notation like [jira:ATL-101], [slack:slack-msg-01], or [gmail:gmail-th-01].
2. Verify message authorship and directionality:
   - When asked if someone (e.g. Person X) sent a message or communicated with the user, carefully check the 'Author' field of each evidence item.
   - Do NOT attribute messages addressed TO Person X (e.g., messages starting with "Hey X..." sent by someone else) as being sent BY Person X.
   - Distinguish incoming communications (authored by others to the user) from outgoing communications (authored by the user/requester to others).
   - If the retrieved evidence only contains outgoing messages sent TO Person X, clearly state that no messages FROM Person X were found, but note the outgoing message sent to them.
3. Point out conflicts or date discrepancies between systems (e.g. if Slack says Sept 10 but Email says Sept 18).
4. Specify who owns blockers and what commitments were made with exact quotes/dates.
5. If something is unknown or missing from evidence, state that plainly.
"""


class AnswerSynthesizer:
    """Synthesizes structured, cited answers from normalized evidence."""

    def __init__(self, llm_client: Optional[ZAIClient] = None):
        self.llm = llm_client or ZAIClient()

    async def synthesize_direct(self, query: str, trace_id: Optional[str] = None) -> AgentResponse:
        """Compose direct answer for general queries that require no enterprise evidence."""
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are ContextMesh, an intelligent AI assistant. "
                    "Answer the user's question directly, clearly, and comprehensively using your general knowledge. "
                    "Do not mention Jira, Slack, or Gmail unless the user explicitly asks about them."
                )
            },
            {"role": "user", "content": query}
        ]

        if self.llm.is_live:
            try:
                answer = await self.llm.generate_chat(prompt, temperature=0.7)
            except Exception as e:
                logger.error(f"LLM direct synthesis failed: {e}")
                raise RuntimeError(f"Failed to generate answer from LLM: {e}")
        else:
            raise RuntimeError(
                "LLM is not configured. Please set ZAI_API_KEY in .secrets/vault or .env to run real reasoning."
            )

        return AgentResponse(
            answer=answer,
            citations=[],
            confidence=1.0,
            contradictions=[],
            trace_id=trace_id,
            requires_approval=False,
            auth_required=False
        )

    async def synthesize(
        self,
        query: str,
        plan: QueryPlan,
        evidence: List[Evidence],
        contradictions: List[Contradiction],
        trace_id: Optional[str] = None,
        tool_failures: Optional[List[Dict[str, Any]]] = None
    ) -> AgentResponse:
        """Compose final cited answer."""
        if not evidence:
            if tool_failures:
                fail_details = "\n".join(f"- **{f.get('tool', 'tool')}**: {f.get('error', 'unknown error')}" for f in tool_failures)
                msg = (
                    "⚠️ Unable to retrieve data due to issues connecting to enterprise services:\n"
                    f"{fail_details}\n\n"
                    "Please check your service credentials or integration settings."
                )
            else:
                msg = "No relevant evidence was found across Jira, Slack, or Gmail to answer your request."

            return AgentResponse(
                answer=msg,
                citations=[],
                confidence=0.1,
                plan=plan,
                contradictions=contradictions,
                trace_id=trace_id
            )

        # Build evidence context block
        evidence_context = []
        for ev in evidence:
            evidence_context.append(
                f"[{ev.evidence_id}] Source: {ev.source.upper()} | Title: {ev.title}\n"
                f"Author: {ev.author or 'Unknown'} | Updated: {ev.updated_at or 'N/A'} | Freshness: {ev.freshness_label}\n"
                f"URL: {ev.source_url}\n"
                f"Content: {ev.content}\n"
            )

        conflict_context = ""
        if contradictions:
            conflict_context = "\nIdentified Contradictions / Staleness Warnings:\n" + "\n".join(
                f"- {c.topic}: {c.description} (Suggestion: {c.resolution_suggestion})"
                for c in contradictions
            )

        user_content = (
            f"User Question: {query}\n\n"
            f"Retrieved Evidence:\n{chr(10).join(evidence_context)}\n"
            f"{conflict_context}\n\n"
            "Synthesize a clear, detailed, cited answer."
        )

        messages = [
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ]

        if self.llm.is_live:
            try:
                raw_answer = await self.llm.generate_chat(messages, temperature=0.2)
                if not raw_answer.strip() or raw_answer.strip().startswith('{"user_intent"'):
                    raw_answer = self._format_evidence_fallback(query, evidence, contradictions)
            except Exception as e:
                logger.warning(f"Synthesis LLM call failed: {e}. Falling back to formatted evidence.")
                raw_answer = self._format_evidence_fallback(query, evidence, contradictions)
        else:
            raw_answer = self._format_evidence_fallback(query, evidence, contradictions)

        # Build structured citations from evidence items
        citations: List[Citation] = []
        for ev in evidence:
            if ev.evidence_id in raw_answer or ev.source_object_id in raw_answer or not self.llm.is_live:
                citations.append(
                    Citation(
                        citation_id=f"cit-{len(citations)+1}",
                        evidence_id=ev.evidence_id,
                        claim=ev.title,
                        source_url=ev.source_url,
                        source_type=ev.source,
                        timestamp=ev.updated_at
                    )
                )

        # Dynamic confidence score based on evidence volume, freshness, and absence of unresolved contradictions
        fresh_count = sum(1 for e in evidence if not e.is_stale)
        confidence = min(0.98, max(0.40, 0.70 + (len(evidence) * 0.04) + (fresh_count * 0.02) - (len(contradictions) * 0.05)))

        return AgentResponse(
            answer=raw_answer,
            citations=citations,
            confidence=round(confidence, 2),
            plan=plan,
            contradictions=contradictions,
            trace_id=trace_id
        )

    def _format_evidence_fallback(self, query: str, evidence: List[Evidence], contradictions: List[Contradiction]) -> str:
        """Format retrieved live evidence into structured markdown when LLM is unconfigured or unavailable."""
        q_lower = query.lower()
        parts = []

        # Find Jira issues / tasks
        jira_items = [ev for ev in evidence if ev.source == "jira"]
        blockers = [ev for ev in jira_items if ev.metadata.get("is_blocker")]
        if any(w in q_lower for w in ["closed", "done", "resolved", "completed"]):
            if jira_items:
                parts.append("### Closed Jira Tasks & Issues")
                for ji in jira_items:
                    status_str = f" | Status: {ji.metadata.get('status')}" if ji.metadata.get('status') else ""
                    assignee_str = f" ({ji.metadata.get('assignee', 'Unassigned')})" if ji.metadata.get('assignee') else ""
                    parts.append(f"- **{ji.source_object_id}**{assignee_str}: {ji.title}{status_str}. [jira:{ji.source_object_id}]")
        elif blockers:
            parts.append("### Active Jira Blockers")
            for b in blockers:
                parts.append(
                    f"- **{b.source_object_id}** ({b.metadata.get('assignee', 'Unassigned')}): {b.title}. "
                    f"Status: {b.metadata.get('status')}. [jira:{b.source_object_id}]"
                )
        elif jira_items:
            parts.append("### Jira Issues & Tasks")
            for ji in jira_items:
                status_str = f" | Status: {ji.metadata.get('status')}" if ji.metadata.get('status') else ""
                assignee_str = f" ({ji.metadata.get('assignee', 'Unassigned')})" if ji.metadata.get('assignee') else ""
                parts.append(f"- **{ji.source_object_id}**{assignee_str}: {ji.title}{status_str}. [jira:{ji.source_object_id}]")

        # Find email communications
        emails = [ev for ev in evidence if ev.source == "gmail"]
        if emails:
            section_title = "Stakeholder Email Commitments & Communications" if ("commit" in q_lower or "priya" in q_lower or "atlas" in q_lower) else "Recent Emails & Communications"
            parts.append(f"\n### {section_title}")
            for em in emails:
                date_str = f" | {em.updated_at}" if em.updated_at else ""
                parts.append(f"- **{em.title}** ({em.author}{date_str}): \"{em.content[:250]}...\" [gmail:{em.source_object_id}]")

        # Find Slack communications & canvases
        slack_items = [ev for ev in evidence if ev.source == "slack"]
        if slack_items:
            parts.append("\n### Relevant Slack Discussions & Canvases")
            for si in slack_items:
                author_str = f" ({si.author})" if si.author else ""
                parts.append(f"- **{si.title}**{author_str}: {si.content[:180]}... [slack:{si.source_object_id}]")

        # Web search results
        web_items = [ev for ev in evidence if ev.source == "web"]
        if web_items:
            parts.append("\n### Web Search Results")
            for wi in web_items:
                link_str = f" ({wi.source_url})" if wi.source_url else ""
                date_str = f" | {wi.updated_at}" if wi.updated_at else ""
                parts.append(f"- **{wi.title}**{link_str}{date_str}: {wi.content[:250]} [web:{wi.source_object_id}]")

        # Include contradictions if any
        if contradictions:
            parts.append("\n### Discrepancies & Freshness Alerts")
            for c in contradictions:
                parts.append(f"- **{c.topic}**: {c.description} *(Action: {c.resolution_suggestion})*")

        return "\n".join(parts) if parts else "No specific evidence was located matching the request criteria."
