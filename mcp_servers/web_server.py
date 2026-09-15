"""MCP Server exposing public web search via Composio (Exa / news search).

Web search is authentication-free (Composio `NO_AUTH` toolkits) and only
requires the global COMPOSIO_API_KEY already used for Jira/Slack/Gmail.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from connectors.base import ConnectorItem, PermissionScope
from mcp_servers.base import MCPToolDefinition, MCPToolResult
from mcp_servers.composio_client import COMPOSIO_CLIENT
from observability.logging import get_logger

logger = get_logger("mcp.web_server")

_NEWS_HINTS = ("news", "headline", "breaking", "current events", "today")


class WebMCPServer:
    """MCP interface for public web search."""

    def get_tool_definitions(self) -> List[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name="web.search",
                description=(
                    "Search the public web for current events, recent news, live data, "
                    "or facts beyond the model's training data. Use for anything that may "
                    "have changed recently (news, prices, releases, sports, weather)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Web search query"},
                        "limit": {"type": "integer", "description": "Max number of results", "default": 5}
                    },
                    "required": ["query"]
                },
                is_mutation=False,
                requires_approval=False
            )
        ]

    async def execute(self, tool_name: str, args: Dict[str, Any], scope: Optional[PermissionScope] = None) -> MCPToolResult:
        try:
            if tool_name != "web.search":
                return MCPToolResult(tool_name=tool_name, success=False, data=None, error=f"Unknown tool: {tool_name}")
            query = str(args.get("query", "")).strip()
            limit = int(args.get("limit", 5) or 5)
            items = await self.search(query, limit)
            return MCPToolResult(tool_name=tool_name, success=True, data=[i.model_dump() for i in items])
        except Exception as e:
            return MCPToolResult(tool_name=tool_name, success=False, data=None, error=str(e))

    async def search(self, query: str, limit: int = 5) -> List[ConnectorItem]:
        """Run a web/news search and normalize results into ConnectorItems."""
        if not query or not COMPOSIO_CLIENT.is_configured():
            if not query:
                return []
            logger.warning("Web search requested but Composio is not configured.")
            return []

        lowered = query.lower()
        is_news = any(hint in lowered for hint in _NEWS_HINTS)

        if is_news:
            try:
                data = await COMPOSIO_CLIENT.call_tool("COMPOSIO_SEARCH_NEWS", {"query": query, "when": "w"})
                items = self._parse_news(data, query, limit)
                if items:
                    return items
            except Exception as e:
                logger.warning(f"Composio news search failed: {e}. Falling back to web search.")

        try:
            data = await COMPOSIO_CLIENT.call_tool("COMPOSIO_SEARCH_WEB", {"query": query})
        except Exception as e:
            logger.warning(f"Composio web search failed: {e}")
            return []

        return self._parse_web(data, query, limit)

    def _as_dict(self, data: Any) -> Dict[str, Any]:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return {"answer": data}
        if isinstance(data, dict):
            nested = data.get("results")
            if isinstance(nested, dict):
                merged = dict(nested)
                merged.setdefault("answer", data.get("answer"))
                return merged
            return data
        return {}

    def _parse_web(self, data: Any, query: str, limit: int) -> List[ConnectorItem]:
        payload = self._as_dict(data)
        answer = payload.get("answer") or payload.get("summary") or ""
        citations = payload.get("citations") or payload.get("organic_results") or []
        if isinstance(citations, dict):
            citations = citations.get("citations") or citations.get("results") or []

        now_iso = datetime.now(timezone.utc).isoformat()
        items: List[ConnectorItem] = []
        seen = set()

        if answer:
            first_url = ""
            if citations and isinstance(citations[0], dict):
                first_url = citations[0].get("url") or citations[0].get("link") or ""
            items.append(
                ConnectorItem(
                    source="web",
                    id="web-answer",
                    title=f"Web search: {query}",
                    content=str(answer),
                    url=first_url,
                    author="Web Search",
                    created_at=now_iso,
                    updated_at=now_iso,
                    raw_payload=payload,
                    metadata={"query": query, "kind": "summary"}
                )
            )

        for idx, cite in enumerate(citations):
            if not isinstance(cite, dict):
                continue
            url = cite.get("url") or cite.get("link") or cite.get("id") or ""
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            title = cite.get("title") or cite.get("name") or f"Web result {idx + 1}"
            snippet = cite.get("snippet") or cite.get("text") or cite.get("summary") or ""
            items.append(
                ConnectorItem(
                    source="web",
                    id=f"web-{idx + 1}",
                    title=title,
                    content=str(snippet or title),
                    url=url,
                    author=cite.get("source") or cite.get("author") or "Web",
                    created_at=cite.get("publishedDate") or cite.get("published_at") or now_iso,
                    updated_at=cite.get("publishedDate") or cite.get("published_at") or "",
                    raw_payload=cite,
                    metadata={"query": query, "kind": "citation"}
                )
            )

        return items[:limit]

    def _parse_news(self, data: Any, query: str, limit: int) -> List[ConnectorItem]:
        payload = self._as_dict(data)
        results = payload.get("news_results") or payload.get("organic_results") or []
        items: List[ConnectorItem] = []
        for idx, res in enumerate(results):
            if not isinstance(res, dict):
                continue
            url = res.get("link") or res.get("url") or ""
            published = res.get("published_at") or res.get("date") or ""
            items.append(
                ConnectorItem(
                    source="web",
                    id=f"web-news-{idx + 1}",
                    title=res.get("title") or f"News result {idx + 1}",
                    content=str(res.get("snippet") or res.get("title") or ""),
                    url=url,
                    author=res.get("source") or "News",
                    created_at=published,
                    updated_at=published,
                    raw_payload=res,
                    metadata={"query": query, "kind": "news"}
                )
            )
        return items[:limit]
