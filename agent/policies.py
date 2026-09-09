"""Security policies, intent & risk classification, and human approval gating."""

import re
from typing import Dict, List, Tuple
from agent.state import QueryPlan, PlanStep


# Injection patterns found in untrusted emails / Notion documents
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous\s+)?instructions",
    r"disregard\s+(all\s+)?prior",
    r"system\s+prompt",
    r"delete\s+all",
    r"drop\s+database",
    r"exfiltrate",
    r"send\s+(credentials|tokens|passwords)\s+to"
]


class PolicyEngine:
    """Enforces enterprise safety, read/write boundaries, and mutation approval gates."""

    @staticmethod
    def sanitize_untrusted_text(text: str) -> str:
        """Strip dangerous prompt-injection directives from untrusted external evidence."""
        sanitized = text
        for pattern in INJECTION_PATTERNS:
            sanitized = re.sub(pattern, "[REDACTED_PROMPT_INJECTION_ATTEMPT]", sanitized, flags=re.IGNORECASE)
        return sanitized

    @staticmethod
    def classify_risk(query: str, plan_steps: List[PlanStep]) -> Tuple[str, bool, List[str]]:
        """
        Determine risk level and approval requirement.
        Returns: (risk_level, requires_approval, reasons)
        """
        reasons = []
        requires_approval = False
        risk_level = "low"

        # Check for mutation tools
        for step in plan_steps:
            if "create" in step.tool or "update" in step.tool or "delete" in step.tool or "mutate" in step.tool:
                requires_approval = True
                risk_level = "high"
                reasons.append(f"Tool '{step.tool}' performs a state mutation.")

        # Check query keywords
        q_lower = query.lower()
        if any(w in q_lower for w in ["create issue", "create task", "delete", "post email", "send email"]):
            requires_approval = True
            risk_level = "high"
            reasons.append("Query requests a modifying action.")

        return risk_level, requires_approval, reasons
