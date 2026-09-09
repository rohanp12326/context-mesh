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
1. Ground every substantive claim in one or more evidence items using bracket notation like [jira:ATL-101] or [gmail:gmail-th-01].
2. Point out conflicts or date discrepancies between systems (e.g. if Notion says Sept 10 but Email says Sept 18).
3. Specify who owns blockers and what commitments were made with exact quotes/dates.
4. If something is unknown or missing from evidence, state that plainly.
"""


class AnswerSynthesizer:
    """Synthesizes structured, cited answers from normalized evidence."""

    def __init__(self, llm_client: Optional[ZAIClient] = None):
        self.llm = llm_client or ZAIClient()

    async def synthesize(
        self,
        query: str,
        plan: QueryPlan,
        evidence: List[Evidence],
        contradictions: List[Contradiction],
        trace_id: Optional[str] = None
    ) -> AgentResponse:
        """Compose final cited answer."""
        if not evidence:
            return AgentResponse(
                answer="No relevant evidence was found across Jira, Notion, or Gmail to answer your request.",
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
                if (
                    raw_answer.strip().startswith('{"user_intent"')
                    or raw_answer.startswith("Mock response")
                    or not raw_answer.strip()
                ):
                    raw_answer = self._offline_synthesize(query, evidence, contradictions)
            except Exception:
                raw_answer = self._offline_synthesize(query, evidence, contradictions)
        else:
            # Deterministic synthesis for offline tests & verification
            raw_answer = self._offline_synthesize(query, evidence, contradictions)

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

        return AgentResponse(
            answer=raw_answer,
            citations=citations,
            confidence=0.92,
            plan=plan,
            contradictions=contradictions,
            trace_id=trace_id
        )

    def _offline_synthesize(self, query: str, evidence: List[Evidence], contradictions: List[Contradiction]) -> str:
        """Deterministic answer generator for offline verification and testing."""
        q_lower = query.lower()
        parts = []

        # Find Jira blockers
        blockers = [ev for ev in evidence if ev.source == "jira" and ev.metadata.get("is_blocker")]
        if blockers:
            parts.append("### Active Jira Blockers")
            for b in blockers:
                parts.append(
                    f"- **{b.source_object_id}** ({b.metadata.get('assignee', 'Unassigned')}): {b.title}. "
                    f"Status: {b.metadata.get('status')}. [jira:{b.source_object_id}]"
                )

        # Find email commitments
        emails = [ev for ev in evidence if ev.source == "gmail"]
        if emails:
            parts.append("\n### Stakeholder Email Commitments & Communications")
            for em in emails:
                parts.append(f"- **{em.title}** ({em.author}): \"{em.content[:200]}...\" [gmail:{em.source_object_id}]")

        # Find Notion specs
        notion_pages = [ev for ev in evidence if ev.source == "notion"]
        if notion_pages:
            parts.append("\n### Relevant Documentation & Specifications")
            for np in notion_pages:
                parts.append(f"- **{np.title}**: {np.content[:180]}... [notion:{np.source_object_id}]")

        # Include contradictions if any
        if contradictions:
            parts.append("\n### Discrepancies & Freshness Alerts")
            for c in contradictions:
                parts.append(f"- **{c.topic}**: {c.description} *(Action: {c.resolution_suggestion})*")

        return "\n".join(parts) if parts else "No specific evidence was located matching the request criteria."
