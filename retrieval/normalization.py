"""Evidence normalization layer across heterogeneous enterprise data sources."""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from connectors.base import ConnectorItem


class Evidence(BaseModel):
    """Normalized evidence record backing an agent conclusion."""
    evidence_id: str
    source: Literal["jira", "slack", "gmail", "memory"]
    source_object_id: str
    source_url: str
    title: str
    content: str
    author: Optional[str] = None
    updated_at: Optional[str] = None
    fetched_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    access_scope: str = "internal"
    content_hash: str
    embedding_version: Optional[str] = "v1"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_stale: bool = False
    freshness_label: str = "Live"


def compute_content_hash(text: str) -> str:
    """Compute sha256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def normalize_connector_item(item: ConnectorItem, scope_name: str = "internal") -> Evidence:
    """Convert any ConnectorItem into a canonical Evidence record."""
    content_hash = compute_content_hash(item.content)
    evidence_id = f"{item.source}:{item.id}"
    
    return Evidence(
        evidence_id=evidence_id,
        source=item.source,  # type: ignore
        source_object_id=item.id,
        source_url=item.url,
        title=item.title,
        content=item.content,
        author=item.author,
        updated_at=item.updated_at,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        access_scope=scope_name,
        content_hash=content_hash,
        metadata=item.metadata
    )


def normalize_evidence_list(items: List[ConnectorItem]) -> List[Evidence]:
    """Normalize a batch of connector items and deduplicate by evidence_id."""
    seen = set()
    normalized = []
    for it in items:
        ev = normalize_connector_item(it)
        if ev.evidence_id not in seen:
            seen.add(ev.evidence_id)
            normalized.append(ev)
    return normalized
