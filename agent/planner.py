"""Query planner decomposing multi-source questions into typed, dependency-aware plans."""

import json
import re
from typing import Dict, List, Optional
from agent.llm import ZAIClient
from agent.state import PlanStep, QueryPlan
from agent.policies import PolicyEngine
from agent.router import AppRouter
from memory.long_term import LongTermMemoryStore


PLANNER_SYSTEM_PROMPT = """You are the Lead Query Planner for ContextMesh, an AI engineering intelligence assistant.
Your goal is to decompose the user's question into an optimal, typed execution plan using Jira, Slack, and Gmail tools.

Available Tools:
- jira.search_issues(query: str, limit: int): Search issues by JQL (e.g. 'project = ATL AND statusCategory != Done') or keyword.
- jira.get_issue(issue_key: str): Fetch specific Jira issue details.
- jira.create_issue(project: str, summary: str, description: str, priority: str): Create Jira issue (MUTATION - requires approval).
- slack.search_messages(query: str, limit: int): Search Slack messages, channels, discussions, and canvases.
- slack.get_thread(thread_id: str): Fetch full Slack thread messages.
- slack.post_message(channel: str, text: str): Post message to a Slack channel (MUTATION - requires approval).
- gmail.search_messages(query: str, limit: int): Search email threads (e.g. 'subject/commitments newer_than:30d').
- gmail.get_thread(thread_id: str): Fetch full thread messages.

Tool Selection & Routing Rules:
1. Zero-Tool Queries: If the query is general knowledge (e.g. "who is the president of america", "how to setup windows 11"), programming trivia, or conversational chit-chat, set "user_intent": "direct_answer" and "steps": []. Do NOT call enterprise tools.
2. Email Queries: If the query asks about emails, mails, or inbox messages (e.g. "what are my recent mails"), select ONLY Gmail tools.
3. Closed Task & Ticket Queries: If the query asks about closed tasks, done tasks, resolved issues, tickets, bugs, or blockers (e.g. "what are my closed tasks", "show completed issues"), select ONLY Jira tools. Jira is the definitive source of truth for task statuses and resolutions. Use JQL: statusCategory = Done or status in (Done, Closed).
4. Opened Tasks & Action Items: If the query asks about open tasks or action items in notes or chats (e.g. "what are my opened tasks"), select Jira and Slack tools.
5. Issue/Ticket Queries: If the query asks about Jira issues, tickets, bugs, or blockers, select ONLY Jira tools.
6. Documentation & Channel Queries: If the query asks about runbooks, specs, canvases, or Slack discussions, select ONLY Slack tools.
7. Cross-Tool Queries: Select multiple tools ONLY when the inquiry requires cross-system correlation.

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
                    resolved["slack_channel"] = r.content.get("slack_channel", "proj-atlas-release")
                    resolved["slack_canvas_id"] = r.content.get("slack_canvas_id", "slack-atlas-spec")

        # Check for people
        if "priya" in query.lower():
            resolved["person_priya"] = "priya.sharma@company.com"
        if "marcus" in query.lower():
            resolved["person_marcus"] = "marcus.vance@company.com"

        return resolved

    async def plan(self, user_query: str) -> QueryPlan:
        """Decompose user query into typed execution plan."""
        # Fast path for zero-tool / general knowledge queries
        if AppRouter.is_zero_tool_query(user_query):
            return QueryPlan(
                user_intent="direct_answer",
                entities={},
                steps=[],
                risk_level="low",
                requires_approval=False
            )

        entities = self.resolve_entities(user_query)
        target_apps = AppRouter.determine_apps(user_query)
        target_guidance = ""
        if len(target_apps) == 1:
            app = target_apps[0]
            target_guidance = f"\nIdentified Target System: {app.upper()} ONLY. Do NOT include tools for any other system unless strictly necessary."

        prompt = f"""User Query: "{user_query}"
Resolved Memory Entities: {json.dumps(entities)}{target_guidance}

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

            # If target_apps is single-app (e.g. jira only for closed tasks or tickets), filter out irrelevant tools
            if len(target_apps) == 1 and target_apps[0] == "jira":
                steps = [s for s in steps if s.tool.startswith("jira.")]
            elif len(target_apps) == 1 and target_apps[0] == "gmail":
                steps = [s for s in steps if s.tool.startswith("gmail.")]
            elif len(target_apps) == 1 and target_apps[0] == "slack":
                steps = [s for s in steps if s.tool.startswith("slack.")]

            # Enforce project key from memory if resolved
            if "project" in entities or "atlas" in user_query.lower():
                jira_key = entities.get("jira_project", "ATL")
                for st in steps:
                    if st.tool.startswith("jira.") and jira_key not in st.query:
                        st.query = f"project = {jira_key} AND ({st.query})" if st.query else f"project = {jira_key}"
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
            # Robust fallback plan if parsing fails using AppRouter
            target_apps = AppRouter.determine_apps(user_query)
            fallback_steps: List[PlanStep] = []
            q_low = user_query.lower()

            if "jira" in target_apps:
                j_q = f"project = {entities.get('jira_project', 'ATL')}" if ("project" in entities or "atlas" in q_low) else ""
                if any(w in q_low for w in ["closed", "done", "resolved", "completed"]):
                    clause = "statusCategory = Done"
                    j_q = f"{j_q} AND {clause}".strip(" AND ") if j_q else clause
                elif any(w in q_low for w in ["open", "opened", "pending", "unresolved"]):
                    clause = "statusCategory != Done"
                    j_q = f"{j_q} AND {clause}".strip(" AND ") if j_q else clause
                fallback_steps.append(
                    PlanStep(
                        id=f"s{len(fallback_steps)+1}",
                        tool="jira.search_issues",
                        query=j_q,
                        purpose="Search related Jira issues" if "closed" not in q_low else "Search closed Jira tasks",
                        arguments={"query": j_q, "limit": 10}
                    )
                )
            if "slack" in target_apps:
                s_q = "Atlas launch plan spec" if ("spec" in q_low or "plan" in q_low) else ("open tasks action items" if "task" in q_low else ("Atlas" if "atlas" in q_low else ""))
                fallback_steps.append(
                    PlanStep(
                        id=f"s{len(fallback_steps)+1}",
                        tool="slack.search_messages",
                        query=s_q,
                        purpose="Search related Slack communications and canvases",
                        arguments={"query": s_q, "limit": 5}
                    )
                )
            if "gmail" in target_apps:
                g_q = "Atlas" if "atlas" in q_low else ""
                fallback_steps.append(
                    PlanStep(
                        id=f"s{len(fallback_steps)+1}",
                        tool="gmail.search_messages",
                        query=g_q,
                        purpose="Search related email communications",
                        arguments={"query": g_q, "limit": 10}
                    )
                )

            risk_level, requires_approval, _ = PolicyEngine.classify_risk(user_query, fallback_steps)
            return QueryPlan(
                user_intent="fallback_search" if fallback_steps else "direct_answer",
                entities=entities,
                steps=fallback_steps,
                risk_level=risk_level,
                requires_approval=requires_approval
            )
