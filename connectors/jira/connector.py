"""Jira connector supporting both offline synthetic dataset and live Jira REST API."""

import json
import os
from typing import Any, Dict, List, Optional
import httpx
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT, is_valid_credential_value
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
        mcp_endpoint: Optional[str] = None,
        mcp_token: Optional[str] = None,
    ):
        super().__init__(mode=mode or "mock", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_base_url = base_url
        self._explicit_user_email = user_email
        self._explicit_api_token = api_token
        self._explicit_mcp_endpoint = mcp_endpoint
        self._explicit_mcp_token = mcp_token
        self._mock_data: Optional[Dict[str, Any]] = None

    @property
    def mode(self) -> str:
        if self._explicit_mode:
            return self._explicit_mode
        return VAULT.get_service_mode("jira")

    @mode.setter
    def mode(self, value: str):
        self._explicit_mode = value

    @property
    def base_url(self) -> str:
        if self._explicit_base_url:
            return self._explicit_base_url.rstrip("/")
        creds = VAULT.get_credential("jira")
        url = creds.get("base_url") or os.getenv("JIRA_BASE_URL", "")
        return url.rstrip("/")

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

    @property
    def mcp_endpoint(self) -> str:
        if self._explicit_mcp_endpoint:
            return self._explicit_mcp_endpoint
        creds = VAULT.get_credential("jira")
        return creds.get("mcp_endpoint") or os.getenv("ATLASSIAN_MCP_ENDPOINT") or "https://mcp.atlassian.com/v2/mcp"

    @property
    def mcp_token(self) -> Optional[str]:
        if self._explicit_mcp_token:
            return self._explicit_mcp_token
        creds = VAULT.get_credential("jira")
        return creds.get("mcp_token") or os.getenv("ATLASSIAN_MCP_TOKEN")

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

    def _search_mock(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search synthetic mock dataset."""
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
            elif "statuscategory = done" in q_lower or "status = done" in q_lower or "status in (done, closed)" in q_lower or "closed" in q_lower:
                if issue.get("status") in ("Done", "Closed", "Resolved"):
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

    def _get_by_id_mock(self, item_id: str) -> Optional[ConnectorItem]:
        """Fetch issue from synthetic mock dataset."""
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

    async def _get_cloud_id(self) -> Optional[str]:
        """Resolve Atlassian cloudId from env, vault, or tenant info."""
        if hasattr(self, "_cached_cloud_id") and self._cached_cloud_id:
            return self._cached_cloud_id
        
        creds = VAULT.get_credential("jira")
        cloud_id = creds.get("cloud_id") or os.getenv("ATLASSIAN_CLOUD_ID")
        if cloud_id:
            self._cached_cloud_id = cloud_id
            return cloud_id

        if self.base_url:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self.base_url}/_edge/tenant_info")
                    if resp.status_code == 200:
                        data = resp.json()
                        cid = data.get("cloudId")
                        if cid:
                            self._cached_cloud_id = cid
                            return cid
            except Exception as e:
                logger.debug(f"Could not resolve cloudId from tenant_info: {e}")
        return None

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:jira" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:jira' scope")

        has_mcp_token = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        has_api_token = bool(self.api_token and is_valid_credential_value(self.api_token) and self.user_email)
        is_mcp_ready = has_mcp_token or has_api_token
        is_configured = is_mcp_ready or VAULT.is_service_authenticated("jira")
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Jira live credentials not configured; falling back to synthetic dataset.")
            return self._search_mock(query, limit)

        # Mode 1: Remote Official Atlassian Rovo Jira MCP Server
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                remote_results = await self._search_remote_mcp(query, limit)
                if remote_results:
                    return remote_results
            except Exception as e:
                logger.warning(f"Jira remote MCP search failed: {e}. Falling back to standard live/mock methods.")

        # Live Jira API implementation if URL and credentials exist
        if self.base_url and self.user_email and self.api_token:
            try:
                headers = {"Accept": "application/json"}
                auth = (self.user_email, self.api_token)
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{self.base_url}/rest/api/3/search/jql",
                        json={
                            "jql": query, 
                            "maxResults": limit, 
                            "fields": ["summary", "description", "status", "priority", "assignee", "reporter", "created", "updated"]
                        },
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
            except Exception as e:
                logger.warning(f"Live Jira REST search failed: {e}. Falling back to mock dataset.")

        return self._search_mock(query, limit)

    async def _search_remote_mcp(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search Jira issues via official Atlassian Rovo MCP server."""
        from mcp_servers.remote_client import RemoteMCPClient

        client = RemoteMCPClient(
            service="jira",
            endpoint_url=self.mcp_endpoint,
            auth_token=self.mcp_token or self.api_token,
            user_email=self.user_email,
        )

        cloud_id = await self._get_cloud_id()
        data = None

        # Priority 1: Official Atlassian Rovo MCP tool
        if cloud_id:
            try:
                data = await client.call_tool("searchJiraIssuesUsingJql", {
                    "cloudId": cloud_id,
                    "jql": query,
                    "maxResults": limit
                })
            except Exception as e:
                logger.debug(f"Official Rovo searchJiraIssuesUsingJql failed: {e}")

        # Priority 2: Fallback tool names
        if not data:
            for tool_name in ["searchJiraIssuesUsingJql", "jira_search_issues", "search_issues", "search"]:
                try:
                    payload = {"query": query, "jql": query, "limit": limit, "maxResults": limit}
                    if cloud_id:
                        payload["cloudId"] = cloud_id
                    data = await client.call_tool(tool_name, payload)
                    if data:
                        break
                except Exception as e:
                    logger.debug(f"Jira MCP tool {tool_name} failed: {e}")
                    continue

        if not data:
            return []

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                pass

        if isinstance(data, dict):
            if data.get("error") or data.get("is_error"):
                logger.warning(f"Atlassian Rovo MCP returned error: {data.get('message') or data}")
                return []

        raw_items = []
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = data.get("issues") or data.get("results") or []

        items: List[ConnectorItem] = []
        for idx, issue in enumerate(raw_items):
            if not isinstance(issue, dict):
                continue
            key = issue.get("key") or issue.get("id") or f"JIRA-{idx}"
            fields = issue.get("fields", {}) if isinstance(issue.get("fields"), dict) else {}
            summary = fields.get("summary") or issue.get("summary") or issue.get("title") or "Jira Issue"
            status = (fields.get("status", {}) if isinstance(fields.get("status"), dict) else {}).get("name") or issue.get("status", "Open")
            priority = (fields.get("priority", {}) if isinstance(fields.get("priority"), dict) else {}).get("name") or issue.get("priority", "Medium")
            assignee = (fields.get("assignee", {}) if isinstance(fields.get("assignee"), dict) else {}).get("emailAddress") or issue.get("assignee", "unassigned")
            desc = fields.get("description") or issue.get("description") or ""

            items.append(
                ConnectorItem(
                    source="jira",
                    id=str(key),
                    title=f"[{key}] {summary}",
                    content=f"Status: {status} | Priority: {priority} | Assignee: {assignee} | Description: {desc}",
                    url=issue.get("url") or f"{self.base_url or 'https://atlassian.net'}/browse/{key}",
                    author=(fields.get("reporter", {}) if isinstance(fields.get("reporter"), dict) else {}).get("emailAddress") or issue.get("reporter"),
                    created_at=fields.get("created") or issue.get("created_at"),
                    updated_at=fields.get("updated") or issue.get("updated_at"),
                    raw_payload=issue,
                    metadata={"status": status, "priority": priority, "assignee": assignee, "mcp": True}
                )
            )
        return items[:limit]

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:jira" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:jira' scope")

        has_mcp_token = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        has_api_token = bool(self.api_token and is_valid_credential_value(self.api_token) and self.user_email)
        is_mcp_ready = has_mcp_token or has_api_token
        is_configured = is_mcp_ready or VAULT.is_service_authenticated("jira")
        if self.mode == "mock" or not is_configured:
            return self._get_by_id_mock(item_id)

        # Remote MCP fetch
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                from mcp_servers.remote_client import RemoteMCPClient
                client = RemoteMCPClient(
                    service="jira",
                    endpoint_url=self.mcp_endpoint,
                    auth_token=self.mcp_token or self.api_token,
                    user_email=self.user_email,
                )
                cloud_id = await self._get_cloud_id()
                data = None
                if cloud_id:
                    try:
                        data = await client.call_tool("getJiraIssue", {"cloudId": cloud_id, "issueIdOrKey": item_id})
                    except Exception:
                        pass

                if not data:
                    for tool_name in ["getJiraIssue", "jira_get_issue", "get_issue"]:
                        try:
                            payload = {"issue_key": item_id, "key": item_id, "id": item_id, "issueIdOrKey": item_id}
                            if cloud_id:
                                payload["cloudId"] = cloud_id
                            data = await client.call_tool(tool_name, payload)
                            if data:
                                break
                        except Exception:
                            continue

                if data:
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except Exception:
                            pass
                    if isinstance(data, dict) and not (data.get("error") or data.get("is_error")):
                        fields = data.get("fields", {}) if isinstance(data.get("fields"), dict) else {}
                        summary = fields.get("summary") or data.get("summary", "")
                        desc = fields.get("description") or data.get("description", "")
                        status = (fields.get("status", {}) if isinstance(fields.get("status"), dict) else {}).get("name") or data.get("status")
                        return ConnectorItem(
                            source="jira",
                            id=str(data.get("key", item_id)),
                            title=f"[{data.get('key', item_id)}] {summary}",
                            content=str(desc),
                            url=data.get("url") or f"{self.base_url}/browse/{data.get('key', item_id)}",
                            author=(fields.get("reporter", {}) if isinstance(fields.get("reporter"), dict) else {}).get("emailAddress") or data.get("reporter"),
                            created_at=fields.get("created") or data.get("created"),
                            updated_at=fields.get("updated") or data.get("updated"),
                            raw_payload=data,
                            metadata={"status": status, "mcp": True}
                        )
            except Exception as e:
                logger.debug(f"Jira MCP get_by_id failed: {e}")

        # Live implementation if URL and credentials configured
        if self.base_url and self.user_email and self.api_token:
            try:
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
            except Exception as e:
                logger.warning(f"Live Jira REST get_by_id failed: {e}. Falling back to mock.")

        return self._get_by_id_mock(item_id)

    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        """Perform a mutation like creating a Jira issue (requires explicit permission)."""
        if scope and not scope.can_mutate:
            raise PermissionError("Permission denied: write mutation requires explicit approval")

        if action == "create_issue":
            has_mcp_token = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
            has_api_token = bool(self.api_token and is_valid_credential_value(self.api_token) and self.user_email)
            is_mcp_ready = has_mcp_token or has_api_token
            if self.mode == "remote_mcp" or is_mcp_ready:
                try:
                    from mcp_servers.remote_client import RemoteMCPClient
                    client = RemoteMCPClient(
                        service="jira",
                        endpoint_url=self.mcp_endpoint,
                        auth_token=self.mcp_token or self.api_token,
                        user_email=self.user_email,
                    )
                    cloud_id = await self._get_cloud_id()
                    data = None
                    if cloud_id:
                        try:
                            mcp_params = dict(params)
                            mcp_params["cloudId"] = cloud_id
                            data = await client.call_tool("createJiraIssue", mcp_params)
                        except Exception:
                            pass

                    if not data:
                        for tool_name in ["createJiraIssue", "jira_create_issue", "create_issue"]:
                            try:
                                mcp_params = dict(params)
                                if cloud_id:
                                    mcp_params["cloudId"] = cloud_id
                                data = await client.call_tool(tool_name, mcp_params)
                                if data:
                                    break
                            except Exception:
                                continue
                    if data and not (isinstance(data, dict) and (data.get("error") or data.get("is_error"))):
                        return data if isinstance(data, dict) else {"status": "created", "result": data}
                except Exception as e:
                    logger.warning(f"Jira remote MCP create_issue failed: {e}. Falling back.")

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
