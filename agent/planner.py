"""Query planner decomposing multi-source questions into typed, dependency-aware plans."""

import json
import re
from typing import Dict, List, Optional
from agent.llm import ZAIClient
from agent.state import PlanStep, QueryPlan
from agent.policies import PolicyEngine
from memory.long_term import LongTermMemoryStore


PLANNER_SYSTEM_PROMPT = """You are the Lead Query Planner for ContextMesh, an AI engineering intelligence assistant.
Your goal is to decompose the user's cross-tool question into an optimal, typed execution plan using Jira, Notion, and Gmail tools.

Available Tools:
- jira.search_issues(query: str, limit: int): Search issues by JQL (e.g. 'project = ATL AND statusCategory != Done') or keyword.
- jira.get_issue(issue_key: str): Fetch specific Jira issue details.
- jira.create_issue(project: str, summary: str, description: str, priority: str): Create Jira issue (MUTATION - requires approval).
- notion.search_pages(query: str, limit: int): Search Notion specs, meeting notes, runbooks.
- notion.get_page_content(page_id: str): Fetch full Notion page.
- gmail.search_messages(query: str, limit: int): Search email threads (e.g. 'subject/commitments newer_than:30d').
- gmail.get_thread(thread_id: str): Fetch full thread messages.

Output strictly valid JSON matching this schema:
{
  "user_intent": "<short_intent_slug>",
  "entities": {"project": "...", "person": "..."},
  "steps": [
    {
      "id": "s1",
      "tool": "jira.search_issues",
      "query": "...",
      "purpose": "...",
      "arguments": {"query": "...", "limit": 10},
      "depends_on": []
    }
  ]
}
"""


class QueryPlanner:
    """Decomposes queries into parallelizable steps and resolves known entities."""

    def __init__(self, llm_client: Optional[ZAIClient] = None, memory_store: Optional[LongTermMemoryStore] = None):
        self.llm = llm_client or ZAIClient()
        self.memory_store = memory_store or LongTermMemoryStore()

    def resolve_entities(self, query: str) -> Dict[str, str]:
        """Lookup stored project aliases, usernames, and domains from long-term memory."""
        resolved: Dict[str, str] = {}
        # Check for Atlas
        if "atlas" in query.lower():
            records = self.memory_store.search_by_entity("Atlas")
            for r in records:
                if r.type == "project_alias":
                    resolved["project"] = "Atlas"
                    resolved["jira_project"] = r.content.get("jira_project", "ATL")
                    resolved["notion_spec_id"] = r.content.get("notion_spec_id", "")

        # Check for people
        if "priya" in query.lower():
            resolved["person_priya"] = "priya.sharma@company.com"
        if "marcus" in query.lower():
            resolved["person_marcus"] = "marcus.vance@company.com"

        return resolved

    async def plan(self, user_query: str) -> QueryPlan:
        """Decompose user query into typed execution plan."""
        entities = self.resolve_entities(user_query)

        prompt = f"""User Query: "{user_query}"
Resolved Memory Entities: {json.dumps(entities)}

Generate the query execution plan in strict JSON.
"""
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]

        raw_output = await self.llm.generate_chat(messages, temperature=0.1)

        # Parse JSON
        try:
            # Extract JSON block if wrapped in markdown
            json_match = re.search(r"\{.*\}", raw_output, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
            else:
                parsed = json.loads(raw_output)

            steps: List[PlanStep] = []
            for s in parsed.get("steps", []):
                args = s.get("arguments", {})
                if "query" not in args:
                    args["query"] = s.get("query", "")
                steps.append(
                    PlanStep(
                        id=s.get("id", f"s{len(steps)+1}"),
                        tool=s.get("tool"),
                        query=s.get("query", ""),
                        purpose=s.get("purpose", ""),
                        arguments=args,
                        depends_on=s.get("depends_on", [])
                    )
                )

            # Enforce project key from memory if resolved
            jira_key = entities.get("jira_project", "ATL")
            for st in steps:
                if st.tool.startswith("jira.") and "ATL" not in st.query:
                    st.query = f"project = {jira_key} AND ({st.query})"
                    st.arguments["query"] = st.query

            risk_level, requires_approval, _ = PolicyEngine.classify_risk(user_query, steps)

            return QueryPlan(
                user_intent=parsed.get("user_intent", "query_decomposition"),
                entities={**entities, **parsed.get("entities", {})},
                steps=steps,
                risk_level=risk_level,
                requires_approval=requires_approval
            )
        except Exception:
            # Robust fallback plan if parsing fails
            fallback_steps = [
                PlanStep(
                    id="s1",
                    tool="jira.search_issues",
                    query=f"project = {entities.get('jira_project', 'ATL')}",
                    purpose="Search related Jira issues",
                    arguments={"query": f"project = {entities.get('jira_project', 'ATL')}", "limit": 10}
                ),
                PlanStep(
                    id="s2",
                    tool="notion.search_pages",
                    query="Atlas",
                    purpose="Search related Notion documentation",
                    arguments={"query": "Atlas", "limit": 5}
                ),
                PlanStep(
                    id="s3",
                    tool="gmail.search_messages",
                    query="Atlas",
                    purpose="Search related email communications",
                    arguments={"query": "Atlas", "limit": 5}
                )
            ]
            risk_level, requires_approval, _ = PolicyEngine.classify_risk(user_query, fallback_steps)
            return QueryPlan(
                user_intent="fallback_search",
                entities=entities,
                steps=fallback_steps,
                risk_level=risk_level,
                requires_approval=requires_approval
            )
