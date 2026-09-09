"""Redaction utilities for PII, secrets, and authorization tokens in telemetry traces."""

import re
from typing import Any, Dict


SECRET_PATTERNS = [
    (r"(Bearer\s+)[A-Za-z0-9\-\._~\+\/]+=*", r"\1[REDACTED_TOKEN]"),
    (r"(api[_-]?key[\"'\s:=]+)[A-Za-z0-9_\-]{8,}", r"\1[REDACTED_API_KEY]"),
    (r"(password[\"'\s:=]+)[^\s,\"']+", r"\1[REDACTED_PASSWORD]"),
    (r"([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", r"[REDACTED_EMAIL]")
]


def redact_sensitive_info(text: str) -> str:
    """Scrub tokens, API keys, passwords, and emails from log strings."""
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted


def sanitize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively scrub sensitive keys and string values in dictionary payloads."""
    clean: Dict[str, Any] = {}
    for k, v in payload.items():
        if any(secret_term in k.lower() for secret_term in ["token", "secret", "password", "key", "auth"]):
            clean[k] = "[REDACTED]"
        elif isinstance(v, str):
            clean[k] = redact_sensitive_info(v)
        elif isinstance(v, dict):
            clean[k] = sanitize_payload(v)
        elif isinstance(v, list):
            clean[k] = [sanitize_payload(i) if isinstance(i, dict) else (redact_sensitive_info(i) if isinstance(i, str) else i) for i in v]
        else:
            clean[k] = v
    return clean
