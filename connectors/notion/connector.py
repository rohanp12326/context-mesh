"""Notion connector supporting synthetic mock dataset and live Notion REST API."""

import json
import os
from typing import Any, Dict, List, Optional
import httpx
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT, is_valid_credential_value
from observability.logging import get_logger

logger = get_logger("connectors.notion")


def _extract_rich_text(block: Dict[str, Any]) -> str:
    """Extract readable text from a Notion block object."""
    btype = block.get("type", "")
    data = block.get(btype, {})
    rich_text = data.get("rich_text", [])
    text_parts = [t.get("plain_text", "") for t in rich_text if "plain_text" in t]
    return "".join(text_parts).strip()


class NotionConnector(BaseConnector):
    """Connector for searching and fetching Notion pages and blocks."""

    def __init__(
        self,
        mode: Optional[str] = None,
        synthetic_data_path: Optional[str] = None,
        api_key: Optional[str] = None,
        mcp_endpoint: Optional[str] = None,
        mcp_token: Optional[str] = None,
    ):
        super().__init__(mode=mode or "mock", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_api_key = api_key
        self._explicit_mcp_endpoint = mcp_endpoint
        self._explicit_mcp_token = mcp_token
        self._mock_data: Optional[Dict[str, Any]] = None

    @property
    def mode(self) -> str:
        if self._explicit_mode:
            return self._explicit_mode
        return VAULT.get_service_mode("notion")

    @mode.setter
    def mode(self, value: str):
        self._explicit_mode = value

    @property
    def api_key(self) -> str:
        if self._explicit_api_key:
            return self._explicit_api_key
        creds = VAULT.get_credential("notion")
        return creds.get("api_key") or os.getenv("NOTION_API_KEY", "")

    @property
    def mcp_endpoint(self) -> str:
        if self._explicit_mcp_endpoint:
            return self._explicit_mcp_endpoint
        creds = VAULT.get_credential("notion")
        return creds.get("mcp_endpoint") or os.getenv("NOTION_MCP_ENDPOINT") or "https://mcp.notion.com/mcp"

    @property
    def mcp_token(self) -> Optional[str]:
        if self._explicit_mcp_token:
            return self._explicit_mcp_token
        creds = VAULT.get_credential("notion")
        return creds.get("mcp_token") or os.getenv("NOTION_MCP_TOKEN")

    def _load_mock_data(self) -> List[Dict[str, Any]]:
        if self._mock_data is not None:
            return self._mock_data.get("notion", {}).get("pages", [])

        data_path = self.synthetic_data_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "evals", "datasets", "synthetic_atlas.json"
        )
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                self._mock_data = json.load(f)
            return self._mock_data.get("notion", {}).get("pages", [])
        return []

    async def _fetch_page_text(self, client: httpx.AsyncClient, page_id: str, headers: Dict[str, str]) -> str:
        """Fetch block children of a Notion page and concatenate plain text."""
        try:
            resp = await client.get(
                f"https://api.notion.com/v1/blocks/{page_id}/children",
                params={"page_size": 25},
                headers=headers,
                timeout=8.0
            )
            if resp.status_code != 200:
                return ""
            blocks_data = resp.json()
            lines = []
            for blk in blocks_data.get("results", []):
                txt = _extract_rich_text(blk)
                if txt:
                    lines.append(txt)
            return "\n".join(lines)
        except Exception:
            return ""

    def _search_mock(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search synthetic mock dataset."""
        pages = self._load_mock_data()
        results = []
        q_lower = query.lower()

        for page in pages:
            haystack = f"{page.get('id', '')} {page.get('title', '')} {page.get('content', '')}".lower()
            words = [w for w in q_lower.split() if len(w) > 2]
            if not words or any(w in haystack for w in words):
                results.append(
                    ConnectorItem(
                        source="notion",
                        id=page["id"],
                        title=page["title"],
                        content=page["content"],
                        url=page.get("url", f"https://notion.so/{page['id']}"),
                        author=page.get("author"),
                        updated_at=page.get("last_edited_time"),
                        raw_payload=page,
                        metadata={"last_edited_time": page.get("last_edited_time")}
                    )
                )
        return results[:limit]

    def _get_by_id_mock(self, item_id: str) -> Optional[ConnectorItem]:
        """Fetch page from synthetic mock dataset."""
        pages = self._load_mock_data()
        for page in pages:
            if page.get("id") == item_id:
                return ConnectorItem(
                    source="notion",
                    id=page["id"],
                    title=page["title"],
                    content=page["content"],
                    url=page.get("url", ""),
                    author=page.get("author"),
                    updated_at=page.get("last_edited_time"),
                    raw_payload=page
                )
        return None

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:notion" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:notion' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_configured = bool(
            is_mcp_ready
            or (self.api_key and is_valid_credential_value(self.api_key))
            or VAULT.is_service_authenticated("notion")
        )
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Notion live credentials not configured; falling back to synthetic dataset.")
            return self._search_mock(query, limit)

        # Mode 1: Remote Official Hosted Notion MCP Server
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                remote_results = await self._search_remote_mcp(query, limit)
                if remote_results:
                    return remote_results
            except Exception as e:
                logger.warning(f"Notion remote MCP search failed: {e}. Falling back to standard live/mock methods.")

        # Live Notion API if key configured
        if self.api_key and is_valid_credential_value(self.api_key):
            headers = {
                "Authorization": f"Bearer {self.api_key.strip()}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.notion.com/v1/search",
                    json={"query": query, "page_size": limit},
                    headers=headers,
                    timeout=12.0
                )
                resp.raise_for_status()
                data = resp.json()
                items = []
                for result in data.get("results", []):
                    title = "Untitled Page"
                    props = result.get("properties", {})
                    for prop in props.values():
                        if prop.get("id") == "title" and prop.get("title"):
                            title = prop["title"][0].get("plain_text", title)
                        elif prop.get("type") == "title" and prop.get("title"):
                            title = prop["title"][0].get("plain_text", title)

                    page_id = result["id"]
                    # Fetch text content from blocks
                    body_text = await self._fetch_page_text(client, page_id, headers)
                    if not body_text:
                        body_text = f"Notion document: {title}"

                    items.append(
                        ConnectorItem(
                            source="notion",
                            id=page_id,
                            title=title,
                            content=body_text,
                            url=result.get("url", f"https://notion.so/{page_id.replace('-', '')}"),
                            updated_at=result.get("last_edited_time"),
                            raw_payload=result,
                            metadata={"last_edited_time": result.get("last_edited_time")}
                        )
                    )
                return items

        return self._search_mock(query, limit)

    async def _search_remote_mcp(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search Notion workspace via official hosted Notion MCP server."""
        from mcp_servers.remote_client import RemoteMCPClient

        client = RemoteMCPClient(
            service="notion",
            endpoint_url=self.mcp_endpoint,
            auth_token=self.mcp_token or self.api_key,
        )

        data = None
        for tool_name in ["search_pages", "search", "query_database"]:
            try:
                data = await client.call_tool(tool_name, {"query": query, "limit": limit, "page_size": limit})
                if data:
                    break
            except Exception as e:
                logger.debug(f"Notion MCP tool {tool_name} failed: {e}")
                continue

        if not data:
            return []

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                pass

        raw_items = []
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = data.get("pages") or data.get("results") or [data]

        items: List[ConnectorItem] = []
        for idx, page in enumerate(raw_items):
            if not isinstance(page, dict):
                continue
            page_id = str(page.get("id") or f"mcp-notion-{idx}")
            title = page.get("title") or page.get("name") or "Notion Document"
            if isinstance(title, list) and title and isinstance(title[0], dict):
                title = title[0].get("plain_text", "Notion Document")
            content = page.get("content") or page.get("text") or page.get("snippet") or f"Notion Document: {title}"
            url = page.get("url") or f"https://notion.so/{page_id.replace('-', '')}"
            last_edited = page.get("last_edited_time") or page.get("updated_at")

            items.append(
                ConnectorItem(
                    source="notion",
                    id=page_id,
                    title=str(title),
                    content=str(content),
                    url=url,
                    author=page.get("author") or page.get("created_by"),
                    updated_at=last_edited,
                    raw_payload=page,
                    metadata={"last_edited_time": last_edited, "mcp": True}
                )
            )
        return items[:limit]

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:notion" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:notion' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_configured = bool(
            is_mcp_ready
            or (self.api_key and is_valid_credential_value(self.api_key))
            or VAULT.is_service_authenticated("notion")
        )
        if self.mode == "mock" or not is_configured:
            return self._get_by_id_mock(item_id)

        # Remote MCP get_page
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                from mcp_servers.remote_client import RemoteMCPClient
                client = RemoteMCPClient(
                    service="notion",
                    endpoint_url=self.mcp_endpoint,
                    auth_token=self.mcp_token or self.api_key,
                )
                for tool_name in ["get_page", "notion_get_page"]:
                    try:
                        data = await client.call_tool(tool_name, {"page_id": item_id, "id": item_id})
                        if data:
                            if isinstance(data, str):
                                try:
                                    data = json.loads(data)
                                except Exception:
                                    pass
                            if isinstance(data, dict):
                                title = data.get("title") or "Notion Page"
                                content = data.get("content") or data.get("snippet") or f"Notion Page: {title}"
                                return ConnectorItem(
                                    source="notion",
                                    id=data.get("id", item_id),
                                    title=str(title),
                                    content=str(content),
                                    url=data.get("url") or f"https://notion.so/{item_id.replace('-', '')}",
                                    updated_at=data.get("last_edited_time"),
                                    raw_payload=data,
                                    metadata={"mcp": True}
                                )
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"Notion MCP get_by_id failed: {e}")

        if self.api_key and is_valid_credential_value(self.api_key):
            headers = {
                "Authorization": f"Bearer {self.api_key.strip()}",
                "Notion-Version": "2022-06-28"
            }
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"https://api.notion.com/v1/pages/{item_id}", headers=headers, timeout=12.0)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                data = resp.json()
                title = "Notion Page"
                props = data.get("properties", {})
                for prop in props.values():
                    if prop.get("id") == "title" and prop.get("title"):
                        title = prop["title"][0].get("plain_text", title)
                    elif prop.get("type") == "title" and prop.get("title"):
                        title = prop["title"][0].get("plain_text", title)

                body_text = await self._fetch_page_text(client, item_id, headers)
                return ConnectorItem(
                    source="notion",
                    id=data["id"],
                    title=title,
                    content=body_text if body_text else f"Notion page: {data.get('url')}",
                    url=data.get("url", f"https://notion.so/{item_id.replace('-', '')}"),
                    updated_at=data.get("last_edited_time"),
                    raw_payload=data
                )

        return self._get_by_id_mock(item_id)

    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        raise NotImplementedError("Mutating Notion pages is currently read-only in this version.")

