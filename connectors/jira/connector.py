"""Jira connector supporting both offline synthetic dataset and live Jira REST API."""

import json
import os
from typing import Any, Dict, List, Optional
import httpx
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT
from observability.logging import get_logger

logger = get_logger("connectors.jira")



class JiraConnector(BaseConnector):
    """Connector for querying and mutating Jira issues."""

    def __init__(
        self,
        mode: Optional[str] = None,
        synthetic_data_path: Optional[str] = None,
        base_url: Optional[str] = None,
        user_email: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        super().__init__(mode=mode or "mock", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_base_url = base_url
        self._explicit_user_email = user_email
        self._explicit_api_token = api_token
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
    def base_url(self) -> str:
        if self._explicit_base_url:
            return self._explicit_base_url
        creds = VAULT.get_credential("jira")
        return creds.get("base_url") or os.getenv("JIRA_BASE_URL", "")

    @property
    def user_email(self) -> str:
        if self._explicit_user_email:
            return self._explicit_user_email
        creds = VAULT.get_credential("jira")
        return creds.get("user_email") or os.getenv("JIRA_USER_EMAIL", "")

    @property
    def api_token(self) -> str:
        if self._explicit_api_token:
            return self._explicit_api_token
        creds = VAULT.get_credential("jira")
        return creds.get("api_token") or os.getenv("JIRA_API_TOKEN", "")

    def _load_mock_data(self) -> List[Dict[str, Any]]:
        if self._mock_data is not None:
            return self._mock_data.get("jira", {}).get("issues", [])

        data_path = self.synthetic_data_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "evals", "datasets", "synthetic_atlas.json"
        )
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                self._mock_data = json.load(f)
            return self._mock_data.get("jira", {}).get("issues", [])
        return []

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:jira" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:jira' scope")

        is_configured = bool(self.base_url and self.user_email and self.api_token)
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Jira live credentials not configured; falling back to synthetic dataset.")
            issues = self._load_mock_data()
            results = []
            q_lower = query.lower()
            
            # Simple keyword / JQL filter simulation for mock mode
            for issue in issues:
                text_to_match = (
                    f"{issue.get('key', '')} {issue.get('summary', '')} "
                    f"{issue.get('description', '')} {issue.get('assignee', '')} "
                    f"{issue.get('status', '')} {issue.get('project', '')}"
                ).lower()
                
                # Check for blocker flag or status queries
                match = False
                if "blocker" in q_lower and issue.get("is_blocker"):
                    match = True
                elif "status != done" in q_lower or "statuscategory != done" in q_lower:
                    if issue.get("status") != "Done":
                        match = True
                elif any(word in text_to_match for word in q_lower.split() if len(word) > 2):
                    match = True
                elif not q_lower.strip():
                    match = True

                if match:
                    results.append(
                        ConnectorItem(
                            source="jira",
                            id=issue["key"],
                            title=f"[{issue['key']}] {issue['summary']}",
                            content=f"Status: {issue.get('status')} | Priority: {issue.get('priority')} | Assignee: {issue.get('assignee')} | Blocker: {issue.get('is_blocker')} | Description: {issue.get('description')}",
                            url=issue.get("url", f"https://jira.example.com/browse/{issue['key']}"),
                            author=issue.get("reporter"),
                            created_at=issue.get("created_at"),
                            updated_at=issue.get("updated_at"),
                            raw_payload=issue,
                            metadata={
                                "project": issue.get("project"),
                                "status": issue.get("status"),
                                "priority": issue.get("priority"),
                                "assignee": issue.get("assignee"),
                                "is_blocker": issue.get("is_blocker", False),
                                "blocks": issue.get("blocks", []),
                            }
                        )
                    )
            return results[:limit]

        # Live Jira API implementation
        headers = {"Accept": "application/json"}
        auth = (self.user_email, self.api_token)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/rest/api/3/search",
                params={"jql": query, "maxResults": limit},
                headers=headers,
                auth=auth,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            items = []
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                items.append(
                    ConnectorItem(
                        source="jira",
                        id=issue.get("key"),
                        title=f"[{issue.get('key')}] {fields.get('summary', '')}",
                        content=str(fields.get("description", "")),
                        url=f"{self.base_url}/browse/{issue.get('key')}",
                        author=fields.get("reporter", {}).get("emailAddress"),
                        created_at=fields.get("created"),
                        updated_at=fields.get("updated"),
                        raw_payload=issue,
                        metadata={
                            "status": fields.get("status", {}).get("name"),
                            "priority": fields.get("priority", {}).get("name"),
                            "assignee": fields.get("assignee", {}).get("emailAddress"),
                        }
                    )
                )
            return items

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:jira" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:jira' scope")

        is_configured = bool(self.base_url and self.user_email and self.api_token)
        if self.mode == "mock" or not is_configured:
            issues = self._load_mock_data()
            for issue in issues:
                if issue.get("key") == item_id:
                    return ConnectorItem(
                        source="jira",
                        id=issue["key"],
                        title=f"[{issue['key']}] {issue['summary']}",
                        content=f"Status: {issue.get('status')} | Assignee: {issue.get('assignee')} | Description: {issue.get('description')}",
                        url=issue.get("url", ""),
                        author=issue.get("reporter"),
                        created_at=issue.get("created_at"),
                        updated_at=issue.get("updated_at"),
                        raw_payload=issue,
                        metadata={"status": issue.get("status"), "assignee": issue.get("assignee")}
                    )
            return None

        # Live implementation
        headers = {"Accept": "application/json"}
        auth = (self.user_email, self.api_token)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/rest/api/3/issue/{item_id}",
                headers=headers,
                auth=auth,
                timeout=10.0,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            issue = resp.json()
            fields = issue.get("fields", {})
            return ConnectorItem(
                source="jira",
                id=issue.get("key"),
                title=f"[{issue.get('key')}] {fields.get('summary', '')}",
                content=str(fields.get("description", "")),
                url=f"{self.base_url}/browse/{issue.get('key')}",
                author=fields.get("reporter", {}).get("emailAddress"),
                created_at=fields.get("created"),
                updated_at=fields.get("updated"),
                raw_payload=issue,
                metadata={"status": fields.get("status", {}).get("name")}
            )

    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        """Perform a mutation like creating a Jira issue (requires explicit permission)."""
        if scope and not scope.can_mutate:
            raise PermissionError("Permission denied: write mutation requires explicit approval")

        if action == "create_issue":
            if self.mode == "mock":
                new_key = f"ATL-{len(self._load_mock_data()) + 101}"
                new_issue = {
                    "key": new_key,
                    "project": params.get("project", "ATL"),
                    "summary": params.get("summary", "New Task"),
                    "description": params.get("description", ""),
                    "status": "To Do",
                    "priority": params.get("priority", "Medium"),
                    "assignee": params.get("assignee", "unassigned"),
                    "created_at": "2026-09-09T12:00:00Z",
                    "updated_at": "2026-09-09T12:00:00Z",
                    "url": f"https://company.atlassian.net/browse/{new_key}"
                }
                if self._mock_data:
                    self._mock_data.setdefault("jira", {}).setdefault("issues", []).append(new_issue)
                return {"status": "created", "issue_key": new_key, "issue": new_issue}

            # Live create issue implementation
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            auth = (self.user_email, self.api_token)
            payload = {
                "fields": {
                    "project": {"key": params.get("project", "ATL")},
                    "summary": params.get("summary"),
                    "description": params.get("description"),
                    "issuetype": {"name": params.get("issue_type", "Task")}
                }
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.base_url}/rest/api/3/issue",
                    json=payload,
                    headers=headers,
                    auth=auth,
                    timeout=10.0
                )
                resp.raise_for_status()
                return resp.json()

        raise ValueError(f"Unsupported mutation action: {action}")
