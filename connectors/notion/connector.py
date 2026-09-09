"""Notion connector supporting synthetic mock dataset and live Notion REST API."""

import json
import os
from typing import Any, Dict, List, Optional
import httpx
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT


from observability.logging import get_logger

logger = get_logger("connectors.notion")


class NotionConnector(BaseConnector):
    """Connector for searching and fetching Notion pages and blocks."""

    def __init__(
        self,
        mode: Optional[str] = None,
        synthetic_data_path: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        super().__init__(mode=mode or "mock", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_api_key = api_key
        self._mock_data: Optional[Dict[str, Any]] = None

    @property
    def mode(self) -> str:
        if self._explicit_mode:
            return self._explicit_mode
        return VAULT.get_connector_mode()

    @mode.setter
    def mode(self, value: str):
        self._explicit_mode = value

    @property
    def api_key(self) -> str:
        if self._explicit_api_key:
            return self._explicit_api_key
        creds = VAULT.get_credential("notion")
        return creds.get("api_key") or os.getenv("NOTION_API_KEY", "")

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

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:notion" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:notion' scope")

        is_configured = bool(self.api_key and self.api_key.strip())
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Notion live credentials not configured; falling back to synthetic dataset.")
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

        # Live Notion API
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.notion.com/v1/search",
                json={"query": query, "page_size": limit},
                headers=headers,
                timeout=10.0
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
                items.append(
                    ConnectorItem(
                        source="notion",
                        id=result["id"],
                        title=title,
                        content=f"Notion page object: {result.get('url')}",
                        url=result.get("url", ""),
                        updated_at=result.get("last_edited_time"),
                        raw_payload=result
                    )
                )
            return items

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:notion" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:notion' scope")

        is_configured = bool(self.api_key and self.api_key.strip())
        if self.mode == "mock" or not is_configured:
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

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": "2022-06-28"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.notion.com/v1/pages/{item_id}", headers=headers, timeout=10.0)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            return ConnectorItem(
                source="notion",
                id=data["id"],
                title="Notion Page",
                content=f"Page url: {data.get('url')}",
                url=data.get("url", ""),
                updated_at=data.get("last_edited_time"),
                raw_payload=data
            )

    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        raise NotImplementedError("Mutating Notion pages is currently read-only in this version.")
