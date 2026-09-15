"""Composio Model Context Protocol (MCP) and Managed Authentication Client.

Provides unified integration with Composio:
1. Managed OAuth Connect Links (`get_auth_url`) for 1-click authentication without manual API tokens.
2. Connection status verification (`check_connection_status`, `list_connected_accounts`).
3. Managed MCP Sessions (`mcp=True`) for tool execution over Model Context Protocol.
4. Direct Tool Execution fallback for Jira, Slack, and Gmail.
"""

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx

from observability.logging import get_logger

logger = get_logger("mcp.composio_client")

COMPOSIO_DEFAULT_BASE_URL = "https://backend.composio.dev/api/v3.1"

# Tokens that carry no real search intent in a "my latest mails" style request.
_GMAIL_FILLER_TOKENS = {
    "latest", "recent", "recently", "new", "newest", "my", "me", "mine",
    "all", "mail", "mails", "email", "emails", "message", "messages",
    "inbox", "show", "list", "get", "fetch", "please", "the", "top",
    "first", "last", "some", "any", "give", "tell", "display", "find",
    "check", "read", "thread", "threads",
}

# Gmail operators that indicate an explicit, intentional query.
_GMAIL_OPERATOR_RE = re.compile(
    r"\b(in|is|has|label|category|subject|from|to|cc|bcc|"
    r"newer_than|older_than|after|before|list|filename|larger|smaller):"
)


def normalize_gmail_query(query: str) -> str:
    """Rewrite vague natural-language mail requests into Gmail search operators.

    Gmail interprets free text as a relevance search, so inputs like
    "latest recent recent" surface stale mail instead of the newest inbox
    messages. Generic recency requests are mapped to the inbox with a
    recency window, while explicit operator queries are preserved verbatim.
    """
    q = (query or "").strip()
    if not q or q == "*":
        return "in:inbox newer_than:7d"

    lowered = q.lower()
    if _GMAIL_OPERATOR_RE.search(lowered):
        return q

    tokens = re.findall(r"[a-z0-9]+", lowered)
    if tokens and all(tok in _GMAIL_FILLER_TOKENS for tok in tokens):
        return "in:inbox newer_than:7d"

    return q


class ComposioMCPClient:
    """Unified client for Composio MCP tool dispatch and streamlined managed authentication."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        user_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self._explicit_api_key = api_key
        self._explicit_base_url = base_url
        self._explicit_user_id = user_id
        self.timeout = timeout
        self._cached_session: Optional[Dict[str, Any]] = None

    @property
    def api_key(self) -> str:
        if self._explicit_api_key is not None:
            return self._explicit_api_key.strip()
        try:
            from security.vault import VAULT
            creds = VAULT.get_credential("composio")
            if creds.get("api_key"):
                return str(creds["api_key"]).strip()
        except Exception:
            pass
        return os.getenv("COMPOSIO_API_KEY", "").strip()

    @property
    def base_url(self) -> str:
        if self._explicit_base_url is not None:
            return self._explicit_base_url.rstrip("/")
        try:
            from security.vault import VAULT
            creds = VAULT.get_credential("composio")
            if creds.get("base_url"):
                return str(creds["base_url"]).rstrip("/")
        except Exception:
            pass
        return os.getenv("COMPOSIO_BASE_URL", COMPOSIO_DEFAULT_BASE_URL).rstrip("/")

    @property
    def user_id(self) -> str:
        if self._explicit_user_id is not None:
            return self._explicit_user_id
        try:
            from security.vault import VAULT
            creds = VAULT.get_credential("composio")
            if creds.get("user_id"):
                return str(creds["user_id"])
        except Exception:
            pass
        return os.getenv("COMPOSIO_USER_ID", "default")

    @property
    def headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ContextMesh-Composio-Client/1.0",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def is_configured(self) -> bool:
        """Return True if a valid Composio API key is present."""
        return bool(self.api_key and not self.api_key.startswith("your_"))

    # -------------------------------------------------------------------------
    # 1. Streamlined Authentication (Connect Links & Status)
    # -------------------------------------------------------------------------

    async def get_auth_url(
        self,
        toolkit: str,
        user_id: Optional[str] = None,
        callback_url: Optional[str] = None,
    ) -> str:
        """Generate a 1-click OAuth Connect Link for Jira, Slack, or Gmail.

        Users open this URL in their browser to authorize access via standard OAuth,
        with Composio securely managing tokens, encryption, and refreshes.
        """
        uid = user_id or self.user_id
        slug = toolkit.lower().strip()

        # Try official Composio SDK first if installed
        try:
            import composio  # type: ignore
            client = composio.Composio(api_key=self.api_key)
            req = client.connected_accounts.link(
                user_id=uid,
                auth_config_id=slug,
                callback_url=callback_url
            )
            redirect_url = getattr(req, "redirect_url", None) or getattr(req, "url", None)
            if redirect_url:
                logger.info(f"Generated Composio auth link for {slug} via SDK: {redirect_url}")
                return str(redirect_url)
        except (ImportError, AttributeError):
            pass
        except Exception as e:
            logger.debug(f"Composio SDK link generation failed: {e}; falling back to HTTP API.")

        # HTTP API Method 1: Active session link via /api/v3.1/tool_router/session/{session_id}/link
        try:
            session_info = await self.get_or_create_mcp_session(user_id=uid)
            sess_id = session_info.get("session_id") or session_info.get("id")
            if sess_id and not sess_id.startswith("session_"):
                link_url = f"{self.base_url}/tool_router/session/{sess_id}/link"
                link_payload: Dict[str, Any] = {"toolkit": slug}
                if callback_url:
                    link_payload["callback_url"] = callback_url
                async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                    resp = await client.post(link_url, json=link_payload)
                    if resp.status_code in (200, 201):
                        data = resp.json()
                        redirect_url = data.get("redirect_url") or data.get("redirectUrl") or data.get("url")
                        if redirect_url:
                            logger.info(f"Generated Composio connect link for {slug} via session link: {redirect_url}")
                            return str(redirect_url)
        except Exception as e:
            logger.debug(f"Session link generation failed: {e}; trying direct auth endpoints.")

        # HTTP API Method 2: Direct connected_accounts link
        endpoints = [
            f"{self.base_url}/connected_accounts/link",
            f"{self.base_url}/connectedAccounts/link",
        ]
        payload = {
            "auth_config_id": slug,
            "user_id": uid,
        }
        if callback_url:
            payload["callback_url"] = callback_url

        last_err = None
        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            for url in endpoints:
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code in (200, 201):
                        data = resp.json()
                        redirect_url = (
                            data.get("redirect_url")
                            or data.get("redirectUrl")
                            or data.get("url")
                            or (data.get("data", {}) if isinstance(data.get("data"), dict) else {}).get("redirect_url")
                        )
                        if redirect_url:
                            logger.info(f"Generated Composio connect link for {slug}: {redirect_url}")
                            return str(redirect_url)
                except Exception as e:
                    last_err = e
                    continue

        if not self.is_configured():
            raise RuntimeError(f"Composio is not configured. Please set COMPOSIO_API_KEY in .secrets/vault or .env to generate auth links for '{slug}'.")

        raise RuntimeError(f"Could not generate Composio auth link for '{slug}': {last_err}")

    async def check_connection_status(
        self,
        toolkit: str,
        user_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Check if an active connected account exists for the given toolkit."""
        uid = user_id or self.user_id
        slug = toolkit.lower().strip()

        # Try Composio SDK if available
        try:
            import composio  # type: ignore
            client = composio.Composio(api_key=self.api_key)
            accounts = client.connected_accounts.list(
                user_ids=[uid],
                toolkit_slugs=[slug],
                statuses=["ACTIVE"]
            )
            if accounts:
                acc_id = getattr(accounts[0], "id", None) or getattr(accounts[0], "nanoid", "active")
                return True, str(acc_id)
        except (ImportError, AttributeError):
            pass
        except Exception as e:
            logger.debug(f"Composio SDK status check failed: {e}; falling back to HTTP API.")

        # HTTP API 1: Check active session toolkits
        try:
            session_info = await self.get_or_create_mcp_session(user_id=uid)
            sess_id = session_info.get("session_id") or session_info.get("id")
            if sess_id and not sess_id.startswith("session_"):
                url = f"{self.base_url}/tool_router/session/{sess_id}/toolkits"
                async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        items = resp.json().get("items", [])
                        for item in items:
                            if item.get("slug") == slug:
                                conn = item.get("connected_account")
                                if conn is not None:
                                    conn_id = conn.get("id") or conn.get("nanoid") or "active"
                                    return True, str(conn_id)
        except Exception as e:
            logger.debug(f"Session toolkits status check failed: {e}")

        # HTTP API 2: Check GET /api/v3.1/connected_accounts
        url = f"{self.base_url}/connected_accounts"
        params = {
            "user_ids": uid,
            "toolkit_slugs": slug,
            "statuses": "ACTIVE"
        }
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items") or data.get("connected_accounts") or (data if isinstance(data, list) else [])
                    for item in items:
                        status = item.get("status", "").upper()
                        if status == "ACTIVE":
                            return True, item.get("id") or item.get("nanoid")
                    return False, None
        except Exception as e:
            logger.debug(f"Composio API status check failed: {e}")

        return False, None

    async def list_connected_accounts(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all connected accounts for the user."""
        uid = user_id or self.user_id
        url = f"{self.base_url}/connected_accounts"
        params = {"user_ids": uid}
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("items") or data.get("connected_accounts") or (data if isinstance(data, list) else [])
        except Exception as e:
            logger.debug(f"Composio list_connected_accounts failed: {e}")
        return []

    def list_connected_accounts_sync(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Synchronously list all connected accounts for the user."""
        uid = user_id or self.user_id
        url = f"{self.base_url}/connected_accounts"
        params = {"user_ids": uid}
        try:
            with httpx.Client(headers=self.headers, timeout=self.timeout) as client:
                resp = client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("items") or data.get("connected_accounts") or (data if isinstance(data, list) else [])
        except Exception as e:
            logger.debug(f"Composio list_connected_accounts_sync failed: {e}")
        return []

    # -------------------------------------------------------------------------
    # 2. Managed MCP Sessions & Tool Execution
    # -------------------------------------------------------------------------

    async def get_or_create_mcp_session(
        self,
        toolkits: Optional[List[str]] = None,
        user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create or reuse a tool router session with Composio."""
        if self._cached_session and (self._cached_session.get("session_id") or self._cached_session.get("id")):
            return self._cached_session

        uid = user_id or self.user_id

        # HTTP API: POST /api/v3.1/tool_router/session
        url = f"{self.base_url}/tool_router/session"
        payload: Dict[str, Any] = {
            "user_id": uid,
        }
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    self._cached_session = data
                    return data
        except Exception as e:
            logger.debug(f"Composio tool_router/session creation failed: {e}")

        # Fallback to legacy /api/v3.1/sessions if tool_router is unavailable
        legacy_url = f"{self.base_url}/sessions"
        legacy_payload = {
            "user_id": uid,
            "toolkits": toolkits or ["jira", "slack", "gmail"],
            "mcp": True,
        }
        try:
            async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                resp = await client.post(legacy_url, json=legacy_payload)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    self._cached_session = data
                    return data
        except Exception as e:
            logger.debug(f"Composio HTTP legacy sessions creation failed: {e}")

        # Fallback MCP session structure
        fallback_url = f"https://backend.composio.dev/v3/mcp?user_id={uid}"
        return {
            "session_id": f"session_{uid}",
            "id": f"session_{uid}",
            "mcp": {
                "url": fallback_url,
                "headers": {"x-api-key": self.api_key},
                "type": "http",
            }
        }

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_id: Optional[str] = None
    ) -> Any:
        """Execute a tool using Composio tool router session or direct execute API."""
        uid = user_id or self.user_id
        resolved_action, mapped_args = self._resolve_action_and_args(tool_name, arguments)
        logger.info(f"Executing Composio tool: requested='{tool_name}' -> action='{resolved_action}'")

        session_info = await self.get_or_create_mcp_session(user_id=uid)
        sess_id = session_info.get("session_id") or session_info.get("id")

        # 1. Primary: POST /api/v3.1/tool_router/session/{session_id}/execute
        if sess_id and not sess_id.startswith("session_"):
            exec_url = f"{self.base_url}/tool_router/session/{sess_id}/execute"
            payload = {
                "tool_slug": resolved_action,
                "arguments": mapped_args,
            }
            try:
                async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
                    resp = await client.post(exec_url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("error"):
                            raise RuntimeError(f"Composio execution error: {data['error']}")
                        logger.info(f"Composio tool executed successfully, log_id={data.get('log_id')}")
                        return data.get("data")
                    elif resp.status_code == 400:
                        err_data = resp.json().get("error", {})
                        msg = err_data.get("message") or resp.text
                        raise RuntimeError(f"Composio execution error (400): {msg}")
            except RuntimeError:
                raise
            except Exception as e:
                logger.debug(f"Tool router session execution failed: {e}; trying fallbacks.")

        # 2. Try MCP SSE Protocol execution if session has an active MCP URL
        mcp_meta = session_info.get("mcp", {})
        mcp_url = mcp_meta.get("url")
        mcp_headers = {**self.headers, **(mcp_meta.get("headers") or {})}

        if mcp_url:
            try:
                from mcp.client.session import ClientSession
                from mcp.client.sse import sse_client
                async with sse_client(mcp_url, headers=mcp_headers, timeout=self.timeout) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as mcp_session:
                        await mcp_session.initialize()
                        result = await mcp_session.call_tool(resolved_action, mapped_args)
                        return self._extract_mcp_result(result)
            except Exception as e:
                logger.debug(f"Composio MCP SSE execution failed ({e}); falling back to tools.execute API.")

        # 3. Direct Composio execution API fallback: POST /api/v3.1/tools/execute
        exec_urls = [
            f"{self.base_url}/tools/execute",
            f"https://backend.composio.dev/api/v1/actions/{resolved_action}/execute",
        ]
        payload = {
            "slug": resolved_action,
            "arguments": mapped_args,
            "user_id": uid,
        }

        async with httpx.AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            for url in exec_urls:
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict):
                            if data.get("successful") is False:
                                err = data.get("error") or "Tool execution reported failure"
                                raise RuntimeError(f"Composio execution error: {err}")
                            return data.get("data") if "data" in data else data
                        return data
                except Exception as e:
                    logger.debug(f"Execution failed on {url}: {e}")
                    continue

        raise RuntimeError(f"Failed to execute Composio tool '{resolved_action}' on all endpoints.")

    def _resolve_action_and_args(self, tool_name: str, args: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Translate ContextMesh standard tool signatures to Composio action slugs and argument formats."""
        t = tool_name.lower().replace(".", "_").replace("-", "_")
        mapped_args = dict(args)

        # Already-resolved Composio action slugs pass through untouched.
        if t.startswith("composio_"):
            return tool_name, mapped_args

        # ----------------- Web Search -----------------
        if ("web" in t and ("search" in t or "browse" in t)) or t in ("search_web", "browse_web", "web_search"):
            q = str(args.get("query") or args.get("q") or "").strip()
            action = "COMPOSIO_SEARCH_NEWS" if any(
                w in q.lower() for w in ("news", "headline", "breaking")
            ) else "COMPOSIO_SEARCH_WEB"
            return action, {"query": q}

        # ----------------- Jira -----------------
        if tool_name == "JIRA_SEARCH_ISSUES_USING_JQL" or ("jira" in t and ("search" in t or "query" in t)):
            q = (args.get("query") or args.get("jql") or "").strip()
            limit = args.get("limit") or args.get("max_results") or args.get("maxResults") or 10
            mapped_args = {
                "max_results": int(limit),
            }
            # Check if q is a structured JQL query or keyword / status filter
            if any(op in q.lower() for op in ["=", "!=", " in ", " is ", "~", "order by"]):
                mapped_args["jql"] = q
            elif q.lower() in ("closed", "done", "completed", "resolved"):
                mapped_args["jql"] = "statusCategory = Done"
                mapped_args["status_id_or_name"] = "Done"
            elif q.lower() in ("open", "opened", "pending", "unresolved"):
                mapped_args["jql"] = "statusCategory != Done"
            elif not q or q.lower() in ("*", "all", "latest", "recent", "issues", "tasks"):
                mapped_args["text_search"] = ""
            else:
                mapped_args["text_search"] = q
            return "JIRA_SEARCH_ISSUES", mapped_args

        if tool_name == "JIRA_GET_ISSUE" or ("jira" in t and ("get_issue" in t or t in ("jira_issue", "jira_get") or "issue_key" in args or "issueIdOrKey" in args)):
            key = args.get("issue_key") or args.get("key") or args.get("issueIdOrKey") or args.get("issue_id_or_key") or args.get("id", "")
            mapped_args = {
                "issue_id_or_key": str(key),
            }
            return "JIRA_GET_ISSUE", mapped_args

        if tool_name == "JIRA_CREATE_ISSUE" or ("jira" in t and "create" in t):
            mapped_args = {
                "project": args.get("project", "ATL"),
                "summary": args.get("summary", ""),
                "description": args.get("description", ""),
                "priority": args.get("priority", "Medium"),
                "issue_type": args.get("issue_type", "Task"),
            }
            return "JIRA_CREATE_ISSUE", mapped_args

        # ----------------- Slack -----------------
        if ("slack" in t or t in ("search_messages", "search_slack", "slack_search")) and (
            "search" in t or ("message" in t and "post" not in t and "thread" not in t)
        ):
            q = (args.get("query") or "").strip()
            if not q or q.lower() in ("latest", "recent", "all", "messages", "*"):
                q = "*"
            mapped_args = {
                "query": q,
                "limit": args.get("limit", 10),
                "count": args.get("limit", 10),
            }
            return "SLACK_SEARCH_MESSAGES", mapped_args

        if "slack" in t and "thread" in t:
            mapped_args = {
                "channel": args.get("channel", ""),
                "thread_ts": args.get("thread_ts") or args.get("thread_id", ""),
            }
            return "SLACK_GET_THREAD", mapped_args

        if "slack" in t and ("post" in t or "send" in t):
            mapped_args = {
                "channel": args.get("channel", ""),
                "text": args.get("text", ""),
            }
            return "SLACK_POST_MESSAGE", mapped_args

        # ----------------- Gmail -----------------
        if "gmail" in t and ("search" in t or "fetch" in t or ("mail" in t and "thread" not in t)):
            mapped_args = {
                "query": normalize_gmail_query(str(args.get("query", ""))),
                "max_results": int(args.get("limit", 10)),
            }
            return "GMAIL_FETCH_EMAILS", mapped_args

        if "gmail" in t and "thread" in t:
            mapped_args = {
                "thread_id": args.get("thread_id") or args.get("id", ""),
            }
            return "GMAIL_GET_THREAD", mapped_args

        # Fallback to uppercase standard slug
        return tool_name.upper().replace(".", "_"), mapped_args

    def _extract_mcp_result(self, result: Any) -> Any:
        """Extract content from MCP CallToolResult object."""
        if hasattr(result, "content") and result.content:
            text_blocks = []
            for item in result.content:
                if hasattr(item, "text"):
                    text_blocks.append(item.text)
                elif isinstance(item, dict) and "text" in item:
                    text_blocks.append(item["text"])
            if text_blocks:
                combined = "\n".join(text_blocks)
                try:
                    return json.loads(combined)
                except Exception:
                    return combined
        return result


# Global singleton instance
COMPOSIO_CLIENT = ComposioMCPClient()
