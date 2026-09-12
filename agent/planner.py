"""Query planner decomposing multi-source questions into typed, dependency-aware plans."""

import json
import re
from typing import Dict, List, Optional
from agent.llm import ZAIClient
from agent.state import PlanStep, QueryPlan
from agent.policies import PolicyEngine
from agent.router import AppRouter
from memory.long_term import LongTermMemoryStore


PLANNER_SYSTEM_PROMPT = """You are the Lead Autonomous Query Planner for ContextMesh, an AI engineering intelligence assistant.
Your goal is to inspect the user's natural language request and synthesize an optimal, typed execution plan using available enterprise tools.

Available Tools:
- jira.search_issues(query: str, limit: int): Search issues by JQL (e.g. 'statusCategory != Done' or 'project = ATL') or search terms.
- jira.get_issue(issue_key: str): Fetch specific Jira issue details.
- jira.create_issue(project: str, summary: str, description: str, priority: str): Create Jira issue (MUTATION - requires approval). Set arguments dynamically based on user request.
- slack.search_messages(query: str, limit: int): Search Slack messages, channels, discussions, and canvases. IMPORTANT: If user asks for latest, recent, or general messages without a specific keyword, use '*' as the query to retrieve all recent messages.
- slack.get_thread(thread_id: str): Fetch full Slack thread messages.
- slack.post_message(channel: str, text: str): Post message to a Slack channel (MUTATION - requires approval).
- gmail.search_messages(query: str, limit: int): Search email threads (e.g. 'newer_than:30d' or keyword).
- gmail.get_thread(thread_id: str): Fetch full email thread messages.

Autonomous Planning Rules:
1. Zero-Tool Queries: If the query is general knowledge (e.g. "who is the president of america", "how to setup windows 11"), programming trivia, or conversational chit-chat, set "user_intent": "direct_answer" and "steps": []. Do NOT call enterprise tools.
2. Single-App Focus: If the query asks about a specific application (e.g. "what is my latest slack messages" -> Slack; "what are my recent mails" -> Gmail; "what are my closed tasks" -> Jira), select ONLY tools for that application.
3. Multi-App / Cross-Tool: If the query inquires about tasks across tools ("what are my opened tasks"), select both Jira and Slack. If checking launch blockers or cross-team updates across mail and chats, select relevant tools.
4. Specific vs Recency Slack Queries: For keyword searches (e.g. "Jira board", "deployment"), search that keyword. For inquiries asking for latest/recent messages without specific text keywords, use query: "*".

Output strictly valid JSON matching this schema:
{
  "user_intent": "<short_intent_slug>",
  "reasoning": "<why you chose these tools and parameters>",
  "entities": {"project": "...", "person": "..."},
  "steps": [
    {
      "id": "s1",
      "tool": "slack.search_messages",
      "query": "*",
      "purpose": "Fetch recent messages across Slack channels",
      "arguments": {"query": "*", "limit": 10},
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
        """Lookup stored project aliases, usernames, and domains dynamically from long-term memory."""
        resolved: Dict[str, str] = {}
        q_lower = query.lower()

        # Dynamic project alias resolution
        for record in self.memory_store.list_records():
            if record.type == "project_alias":
                alias_name = str(record.content.get("alias", "")).lower()
                if alias_name and alias_name in q_lower:
                    resolved["project"] = record.content.get("alias", "")
                    for k, v in record.content.items():
                        resolved[k] = str(v)
            elif record.type == "role_mapping":
                full_name = str(record.content.get("name", "")).lower()
                first_name = full_name.split()[0] if full_name else ""
                if (first_name and first_name in q_lower) or (full_name and full_name in q_lower):
                    key_suffix = first_name or "person"
                    resolved[f"person_{key_suffix}"] = record.content.get("email", "")
                    resolved[f"role_{key_suffix}"] = record.content.get("domain", "")

        # Keyword token search across entities for any words > 3 chars
        tokens = re.findall(r"[a-zA-Z0-9_-]+", query)
        for token in tokens:
            if len(token) > 3 and token.lower() not in ("what", "show", "find", "check", "tasks", "task", "issues"):
                records = self.memory_store.search_by_entity(token)
                for r in records:
                    if r.type == "project_alias" and "project" not in resolved:
                        resolved["project"] = r.content.get("alias", token)
                        for k, v in r.content.items():
                            resolved[k] = str(v)
                    elif r.type == "role_mapping":
                        resolved[f"entity_{token.lower()}"] = str(r.content.get("email") or r.content.get("name"))

        return resolved

    def build_memory_context(self, query: str) -> str:
        """Format matching organizational memory into prompt context."""
        resolved = self.resolve_entities(query)
        lines = []
        if resolved:
            lines.append("Active Known Entities from Memory:")
            for k, v in resolved.items():
                lines.append(f"- {k}: {v}")

        # Also list active project aliases
        aliases = [r for r in self.memory_store.list_records() if r.type == "project_alias"]
        if aliases:
            lines.append("Known Project Aliases:")
            for a in aliases:
                lines.append(f"- Alias '{a.content.get('alias')}': Jira Project '{a.content.get('jira_project')}', Slack '{a.content.get('slack_channel')}'")

        return "\n".join(lines) if lines else "No prior project memory for this query."

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
            cleaned = raw_output.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"\s*```$", "", cleaned)
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
            else:
                parsed = json.loads(cleaned)

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

            # Handle mutation requests (e.g. create task/issue)
            if any(w in q_low for w in ["create", "open", "file", "add"]) and any(w in q_low for w in ["task", "ticket", "issue", "bug"]):
                summary = re.sub(r"^(create|add|open|make|file)\s+(a\s+)?(new\s+)?(jira\s+)?(ticket|task|issue|bug)\s+(for|to|about)?\s*", "", user_query, flags=re.IGNORECASE).strip()
                summary = summary or "New Task"
                priority = "High" if "high" in q_low else ("Critical" if "critical" in q_low else "Medium")
                proj = entities.get("jira_project", "ATL")
                fallback_steps.append(
                    PlanStep(
                        id="s1",
                        tool="jira.create_issue",
                        query=f"project = {proj}",
                        purpose=f"Create Jira ticket: {summary}",
                        arguments={
                            "project": proj,
                            "summary": summary,
                            "description": user_query,
                            "priority": priority
                        }
                    )
                )

            if not fallback_steps:
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
                    # If specific topic mentioned, use it; otherwise wildcard * for latest/recent messages
                    if "spec" in q_low or "plan" in q_low:
                        s_q = "Atlas launch plan spec" if "atlas" in q_low else "launch plan spec"
                    elif "task" in q_low or "action item" in q_low:
                        s_q = "open tasks action items"
                    elif "atlas" in q_low:
                        s_q = "Atlas"
                    else:
                        s_q = "*"
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
                    g_q = "Atlas" if "atlas" in q_low else ("from:priya OR from:marcus" if ("priya" in q_low or "marcus" in q_low) else "")
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
