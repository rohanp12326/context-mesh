"""App Determination and Query Intent Router for ContextMesh.

Classifies incoming user queries into:
1. Zero-Tool Queries: General knowledge, technical how-tos, math, coding, chit-chat
   that do NOT require enterprise tool execution.
2. App-Specific Queries: Intelligently routes to Gmail, Jira, Slack, or combinations
   (e.g., 'recent mails' -> Gmail only; 'opened tasks' -> Jira + Slack).
"""

import re
from typing import List, Set


# Patterns identifying general knowledge or chit-chat queries that don't need enterprise tools
ZERO_TOOL_PATTERNS = [
    # General informational queries
    r"^(who|what|where|when|why|how)\s+(is|was|are|were)\s+(the\s+)?(president|prime minister|capital|currency|population|weather|founder|ceo)\b",
    r"^who\s+is\s+[A-Za-z0-9\s]+\??$",
    r"^how\s+to\s+(setup|set\s+up|install|configure|format|reinstall)\s+(windows|mac|linux|ubuntu|ios|android|python|git|node|npm|docker)\b",
    r"^(what|explain|how\s+does)\s+(is\s+)?(quantum\s+computing|photosynthesis|relativity|machine\s+learning|dns|http|tcp|rest\s+api)\b",
    # Conversational greetings & chit-chat
    r"^(hi|hello|hey|greetings|good\s+(morning|afternoon|evening)|howdy)\b",
    r"^(how\s+are\s+you|what\s+can\s+you\s+do|who\s+are\s+you|help)\b",
    r"^(tell\s+me\s+a\s+joke|thank\s+you|thanks|bye|goodbye)\b",
    # Math & coding trivia
    r"^(\d+\s*[\+\-\*\/\^]\s*\d+|\bcalculate\b|\bsolve\b)",
    r"^(write|code)\s+a\s+(python|javascript|java|c\+\+|bash)\s+(function|script|algorithm)\s+to\b",
]

# Patterns for specific application domains
GMAIL_KEYWORDS = {
    "mail", "mails", "email", "emails", "inbox", "sent", "thread", "threads",
    "message", "messages", "sender", "recipient", "attachment", "newsletter",
    "unread", "subject"
}

JIRA_KEYWORDS = {
    "jira", "ticket", "tickets", "issue", "issues", "bug", "bugs",
    "backlog", "sprint", "epic", "epics", "story", "stories", "blocker",
    "blockers", "assignee", "jql", "story points",
    "task", "tasks", "closed", "done", "resolved", "completed"
}

SLACK_KEYWORDS = {
    "slack", "channel", "channels", "thread", "threads", "chat", "message", "messages",
    "canvas", "canvases", "huddle", "announcement", "announcements",
    "doc", "docs", "spec", "specs", "specification", "specifications",
    "document", "documents", "wiki", "runbook", "runbooks",
    "meeting notes", "minutes", "architecture", "guideline", "guidelines"
}

CLOSED_TASK_PATTERNS = [
    r"\b(closed|done|completed|resolved)\s+(tasks?|issues?|tickets?|stories|work|bugs?)\b",
    r"\b(my\s+)?(closed|done|completed|resolved)\s+tasks?\b",
    r"\bwhat\s+(are\s+my|do\s+i\s+have)\s+(closed|done|completed|resolved)\s+tasks?\b",
    r"\btasks?\s+(that\s+are\s+)?(closed|done|completed|resolved)\b",
]

TASK_PATTERNS = [
    r"\b(open|opened|pending|my|assigned|active|unresolved)\s+tasks?\b",
    r"\baction\s+items?\b",
    r"\bto-?dos?\b",
    r"\bwhat\s+(are\s+my|do\s+i\s+have)\s+tasks?\b",
    r"\btasks?\s+(assigned|pending|opened)\b",
]


class AppRouter:
    """Intelligent query intent classifier and app routing engine."""

    @classmethod
    def is_zero_tool_query(cls, query: str) -> bool:
        """Return True if query is general knowledge or conversational and needs no tools."""
        q = query.strip().lower()

        # If it explicitly mentions our known tools or project entities, it's not zero-tool
        if any(w in q for w in ["jira", "slack", "gmail", "atlas", "atl-", "priya", "marcus"]):
            return False

        # Common general queries
        if "president of" in q or "windows 11" in q or "windows 10" in q:
            return True

        # Check regex patterns
        for pattern in ZERO_TOOL_PATTERNS:
            if re.search(pattern, q, re.IGNORECASE):
                return True

        return False

    @classmethod
    def determine_apps(cls, query: str) -> List[str]:
        """Determine which enterprise apps should be queried.
        
        Returns a list of app identifiers: e.g. ['gmail'], ['jira', 'slack'], etc.
        Returns empty list [] for zero-tool / general queries.
        """
        if cls.is_zero_tool_query(query):
            return []

        q = query.lower()
        words = set(re.findall(r"\b[a-z0-9_-]+\b", q))

        # Check for closed / completed / resolved tasks -> Jira ONLY
        for ctp in CLOSED_TASK_PATTERNS:
            if re.search(ctp, q):
                return ["jira"]

        # Check for opened tasks / action items -> Jira + Slack
        for tp in TASK_PATTERNS:
            if re.search(tp, q):
                return ["jira", "slack"]

        target_apps: Set[str] = set()

        # Check Gmail keywords
        if any(k in words for k in GMAIL_KEYWORDS) or "email" in q or "mail" in q:
            target_apps.add("gmail")

        # Check Jira keywords
        if any(k in words for k in JIRA_KEYWORDS) or "jira" in q or "atl-" in q:
            target_apps.add("jira")

        # Check Slack keywords
        if any(k in words for k in SLACK_KEYWORDS) or "slack" in q or "runbook" in q or "canvas" in q:
            target_apps.add("slack")

        # Entity-based routing:
        # Priya or Marcus commitments in email
        if ("priya" in q or "marcus" in q) and ("commit" in q or "promise" in q or "said" in q):
            target_apps.add("gmail")

        # Release status / delay / blocker queries (e.g. Project Atlas release or launch delay)
        if "release" in q or "block" in q or "delay" in q or "launch" in q:
            target_apps.add("jira")
            if "slack" in q or "spec" in q or "plan" in q or "delay" in q:
                target_apps.add("slack")
            if "email" in q or "mail" in q or "commit" in q or "conflict" in q:
                target_apps.add("gmail")

        # Decisions / meeting discussions
        if "decision" in q:
            if "slack" in q or "documented" in q:
                target_apps.add("slack")
            if "email" in q or "mail" in q or "discussed" in q:
                target_apps.add("gmail")

        # If query specifically mentions Atlas without app keywords, default to Jira + Slack
        if "atlas" in q and not target_apps:
            target_apps.update(["jira", "slack"])

        # If still empty and not a zero-tool query, fallback to searching all
        if not target_apps:
            target_apps.update(["jira", "slack", "gmail"])

        return sorted(list(target_apps))
