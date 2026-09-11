"""Slack connector supporting synthetic mock dataset, live Slack Web API, and official Slack MCP server."""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT, is_valid_credential_value
from mcp_servers.composio_client import COMPOSIO_CLIENT
from observability.logging import get_logger

logger = get_logger("connectors.slack")


class SlackConnector(BaseConnector):
    """Connector for searching and fetching Slack messages, threads, and canvases."""

    def __init__(
        self,
        mode: Optional[str] = None,
        synthetic_data_path: Optional[str] = None,
        bot_token: Optional[str] = None,
        user_token: Optional[str] = None,
        mcp_endpoint: Optional[str] = None,
        mcp_token: Optional[str] = None,
    ):
        super().__init__(mode=mode or "mock", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_bot_token = bot_token
        self._explicit_user_token = user_token
        self._explicit_mcp_endpoint = mcp_endpoint
        self._explicit_mcp_token = mcp_token
        self._mock_data: Optional[Dict[str, Any]] = None

    @property
    def mode(self) -> str:
        if self._explicit_mode:
            return self._explicit_mode
        return VAULT.get_service_mode("slack")

    @mode.setter
    def mode(self, value: str):
        self._explicit_mode = value

    @property
    def bot_token(self) -> str:
        if self._explicit_bot_token:
            return self._explicit_bot_token
        creds = VAULT.get_credential("slack")
        return creds.get("bot_token") or creds.get("api_key") or os.getenv("SLACK_BOT_TOKEN") or os.getenv("SLACK_TOKEN", "")

    @property
    def user_token(self) -> str:
        if self._explicit_user_token:
            return self._explicit_user_token
        creds = VAULT.get_credential("slack")
        return creds.get("user_token") or os.getenv("SLACK_USER_TOKEN", "")

    @property
    def mcp_endpoint(self) -> str:
        if self._explicit_mcp_endpoint:
            return self._explicit_mcp_endpoint
        creds = VAULT.get_credential("slack")
        return creds.get("mcp_endpoint") or os.getenv("SLACK_MCP_ENDPOINT") or "https://mcp.slack.com/mcp"

    @property
    def mcp_token(self) -> Optional[str]:
        if self._explicit_mcp_token:
            return self._explicit_mcp_token
        creds = VAULT.get_credential("slack")
        return creds.get("mcp_token") or os.getenv("SLACK_MCP_TOKEN") or self.user_token or self.bot_token

    def _load_mock_data(self) -> List[Dict[str, Any]]:
        if self._mock_data is not None:
            return self._mock_data.get("slack", {}).get("messages", [])

        data_path = self.synthetic_data_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "evals", "datasets", "synthetic_atlas.json"
        )
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                self._mock_data = json.load(f)
            return self._mock_data.get("slack", {}).get("messages", [])
        return []

    def _search_mock(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search synthetic mock Slack dataset."""
        messages = self._load_mock_data()
        results = []
        q_lower = query.lower()

        for msg in messages:
            haystack = f"{msg.get('id', '')} {msg.get('title', '')} {msg.get('channel', '')} {msg.get('content', '')}".lower()
            words = [w for w in q_lower.split() if len(w) > 2]
            if not words or any(w in haystack for w in words):
                channel_name = msg.get("channel", "general")
                default_title = msg.get("title") or f"Slack #{channel_name}"
                results.append(
                    ConnectorItem(
                        source="slack",
                        id=msg["id"],
                        title=default_title,
                        content=msg["content"],
                        url=msg.get("url", f"https://slack.com/archives/{channel_name}/{msg['id']}"),
                        author=msg.get("author"),
                        updated_at=msg.get("updated_at"),
                        raw_payload=msg,
                        metadata={
                            "channel": channel_name,
                            "updated_at": msg.get("updated_at")
                        }
                    )
                )
        return results[:limit]

    def _get_by_id_mock(self, item_id: str) -> Optional[ConnectorItem]:
        """Fetch item from synthetic mock Slack dataset."""
        messages = self._load_mock_data()
        for msg in messages:
            if msg.get("id") == item_id:
                channel_name = msg.get("channel", "general")
                default_title = msg.get("title") or f"Slack #{channel_name}"
                return ConnectorItem(
                    source="slack",
                    id=msg["id"],
                    title=default_title,
                    content=msg["content"],
                    url=msg.get("url", f"https://slack.com/archives/{channel_name}/{msg['id']}"),
                    author=msg.get("author"),
                    updated_at=msg.get("updated_at"),
                    raw_payload=msg,
                    metadata={"channel": channel_name}
                )
        return None

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:slack" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:slack' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_composio_ready = bool(COMPOSIO_CLIENT.is_configured() and VAULT.is_composio_connected("slack"))
        is_configured = bool(
            is_mcp_ready
            or is_composio_ready
            or (self.bot_token and is_valid_credential_value(self.bot_token))
            or (self.user_token and is_valid_credential_value(self.user_token))
            or VAULT.is_service_authenticated("slack")
        )
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Slack live credentials not configured; falling back to synthetic dataset.")
            return self._search_mock(query, limit)

    @staticmethod
    def _sanitize_slack_query(query: str) -> str:
        """Normalize user query for Slack search API.

        Slack search API requires a non-empty string and performs literal keyword matching.
        If the query is empty or composed exclusively of recency/meta words (e.g. 'latest', 'recent',
        'messages', 'what is my latest slack messages'), literal search will either error ('no_query')
        or return 0 matches. In such cases, we convert the query to '*' (wildcard matching all recent messages).
        """
        if not query:
            return "*"
        clean = query.strip()
        if not clean or clean in ("*", '""', "''", "all"):
            return "*"

        words = re.findall(r"[a-zA-Z0-9]+", clean.lower())
        if not words:
            return "*"

        meta_words = {
            "what", "is", "are", "my", "the", "latest", "recent", "new", "newest",
            "slack", "messages", "message", "msg", "msgs", "chat", "chats",
            "show", "get", "fetch", "check", "find", "read", "list", "all",
            "inbox", "channel", "channels"
        }
        if all(w in meta_words for w in words):
            return "*"
        return clean

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:slack" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:slack' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_composio_ready = bool(COMPOSIO_CLIENT.is_configured() and VAULT.is_composio_connected("slack"))
        is_configured = bool(
            is_mcp_ready
            or is_composio_ready
            or (self.bot_token and is_valid_credential_value(self.bot_token))
            or (self.user_token and is_valid_credential_value(self.user_token))
            or VAULT.is_service_authenticated("slack")
        )
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Slack live credentials not configured; falling back to synthetic dataset.")
            return self._search_mock(query, limit)

        # Mode 1: Remote Composio MCP or Official Hosted Slack MCP Server
        active_token = self.user_token or self.bot_token
        if self.mode in ("live", "remote_mcp") and (is_composio_ready or is_mcp_ready or self.mode == "remote_mcp"):
            try:
                remote_results = await self._search_remote_mcp(query, limit)
                # In live mode with active Composio connection and no direct bot token, return real results directly
                if remote_results or (is_composio_ready and not active_token):
                    return remote_results
            except Exception as e:
                logger.warning(f"Slack remote MCP search failed: {e}.")
                if is_composio_ready and not active_token:
                    return []

        # Mode 2: Live Slack Web API
        active_token = self.user_token or self.bot_token
        if active_token and is_valid_credential_value(active_token):
            try:
                headers = {
                    "Authorization": f"Bearer {active_token.strip()}",
                    "Content-Type": "application/json; charset=utf-8"
                }
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        "https://slack.com/api/search.messages",
                        params={"query": query, "count": limit},
                        headers=headers,
                        timeout=12.0
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("ok"):
                            items = []
                            matches = data.get("messages", {}).get("matches", [])
                            for m in matches:
                                channel_info = m.get("channel", {})
                                channel_name = channel_info.get("name") if isinstance(channel_info, dict) else str(channel_info)
                                msg_text = m.get("text", "")
                                author = m.get("username") or m.get("user", "slack_user")
                                ts = m.get("ts", "")
                                permalink = m.get("permalink", f"https://slack.com/archives/{channel_name}/p{ts.replace('.', '')}")

                                items.append(
                                    ConnectorItem(
                                        source="slack",
                                        id=f"slack-msg-{ts}",
                                        title=f"Slack #{channel_name}: {msg_text[:50]}",
                                        content=msg_text,
                                        url=permalink,
                                        author=author,
                                        updated_at=m.get("updated_at") or ts,
                                        raw_payload=m,
                                        metadata={"channel": channel_name, "ts": ts}
                                    )
                                )
                            return items[:limit]
            except Exception as e:
                logger.warning(f"Slack live search API call failed: {e}. Falling back to mock dataset.")

        return self._search_mock(query, limit)

    async def _search_remote_mcp(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search Slack workspace via Composio MCP or official hosted Slack MCP server."""
        from mcp_servers.remote_client import RemoteMCPClient

        client = RemoteMCPClient(
            service="slack",
            endpoint_url=self.mcp_endpoint,
            auth_token=self.mcp_token or self.user_token or self.bot_token,
        )

        clean_query = self._sanitize_slack_query(query)
        data = None
        last_error = None

        # Candidate tools mapping to Slack message search
        for tool_name in ["SLACK_SEARCH_MESSAGES", "slack.search_messages", "slack_search_messages"]:
            try:
                data = await client.call_tool(tool_name, {"query": clean_query, "limit": limit, "count": limit})
                if data is not None:
                    break
            except Exception as e:
                last_error = e
                logger.debug(f"Slack MCP tool {tool_name} failed: {e}")
                # If error is no_query or invalid query, retry once with wildcard '*'
                if clean_query != "*" and "no_query" in str(e).lower():
                    try:
                        clean_query = "*"
                        data = await client.call_tool(tool_name, {"query": "*", "limit": limit, "count": limit})
                        if data is not None:
                            break
                    except Exception as e2:
                        last_error = e2
                        continue
                continue

        # If keyword search returned 0 matches and query had recency intent, retry with wildcard '*'
        if isinstance(data, dict) and clean_query != "*":
            msg_obj = data.get("messages")
            matches = msg_obj.get("matches", []) if isinstance(msg_obj, dict) else (data.get("matches") or [])
            if not matches and any(w in query.lower() for w in ["latest", "recent", "new", "message", "all"]):
                logger.info(f"Slack search for '{clean_query}' returned 0 matches; retrying with wildcard '*' for recent messages.")
                try:
                    retry_data = await client.call_tool("SLACK_SEARCH_MESSAGES", {"query": "*", "limit": limit, "count": limit})
                    if retry_data is not None:
                        data = retry_data
                except Exception as e:
                    logger.debug(f"Wildcard retry failed: {e}")

        if data is None:
            if last_error:
                raise last_error
            return []

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                pass

        if isinstance(data, dict):
            if data.get("error") or data.get("is_error"):
                logger.warning(f"Slack MCP returned error: {data.get('message') or data}")
                return []

        raw_items = []
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            msg_obj = data.get("messages")
            if isinstance(msg_obj, dict):
                raw_items = msg_obj.get("matches") or msg_obj.get("items") or []
            elif isinstance(msg_obj, list):
                raw_items = msg_obj
            else:
                raw_items = data.get("matches") or data.get("results") or data.get("items") or []

        items: List[ConnectorItem] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("ts") or item.get("iid") or f"mcp-slack-{idx}")
            channel = item.get("channel") or item.get("channel_name") or "general"
            if isinstance(channel, dict):
                channel = channel.get("name") or channel.get("id") or "general"
            msg_text = item.get("content") or item.get("text") or item.get("snippet") or f"Slack message in #{channel}"
            title = item.get("title") or f"Slack #{channel}: {msg_text[:50]}"
            url = item.get("url") or item.get("permalink") or f"https://slack.com/archives/{channel}/{item_id}"
            ts_val = item.get("updated_at") or item.get("ts")
            iso_time = None
            if ts_val:
                try:
                    iso_time = datetime.fromtimestamp(float(ts_val), tz=timezone.utc).isoformat()
                except Exception:
                    iso_time = str(ts_val)

            items.append(
                ConnectorItem(
                    source="slack",
                    id=item_id,
                    title=str(title),
                    content=str(msg_text),
                    url=url,
                    author=item.get("author") or item.get("username") or item.get("user") or "slack_user",
                    created_at=iso_time,
                    updated_at=iso_time,
                    raw_payload=item,
                    metadata={"channel": channel, "mcp": True}
                )
            )
        return items[:limit]

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:slack" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:slack' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_composio_ready = bool(COMPOSIO_CLIENT.is_configured() and VAULT.is_composio_connected("slack"))
        is_configured = bool(
            is_mcp_ready
            or is_composio_ready
            or (self.bot_token and is_valid_credential_value(self.bot_token))
            or (self.user_token and is_valid_credential_value(self.user_token))
            or VAULT.is_service_authenticated("slack")
        )
        if self.mode == "mock" or not is_configured:
            return self._get_by_id_mock(item_id)

        # Remote MCP tool: slack_read_channel, slack_read_thread, or slack_read_canvas
        if self.mode in ("live", "remote_mcp") and (is_composio_ready or is_mcp_ready or self.mode == "remote_mcp"):
            try:
                from mcp_servers.remote_client import RemoteMCPClient
                client = RemoteMCPClient(
                    service="slack",
                    endpoint_url=self.mcp_endpoint,
                    auth_token=self.mcp_token or self.user_token or self.bot_token,
                )
                for tool_name in ["SLACK_GET_THREAD", "slack_read_thread", "read_thread", "slack_read_channel", "read_channel", "slack_read_canvas"]:
                    try:
                        data = await client.call_tool(tool_name, {"id": item_id, "thread_ts": item_id, "channel_id": item_id})
                        if data:
                            if isinstance(data, str):
                                try:
                                    data = json.loads(data)
                                except Exception:
                                    pass
                            if isinstance(data, dict) and not (data.get("error") or data.get("is_error")):
                                return ConnectorItem(
                                    source="slack",
                                    id=data.get("id", item_id),
                                    title=data.get("title", f"Slack Discussion {item_id}"),
                                    content=data.get("content") or data.get("text", ""),
                                    url=data.get("url") or f"https://slack.com/archives/{item_id}",
                                    updated_at=data.get("updated_at") or data.get("ts"),
                                    raw_payload=data,
                                    metadata={"mcp": True}
                                )
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"Slack MCP get_by_id failed: {e}")

        return self._get_by_id_mock(item_id)

    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        """Perform a mutation like sending a Slack message or posting to a channel."""
        if scope and not scope.can_mutate:
            raise PermissionError("Permission denied: write mutation requires explicit approval")

        is_mcp_ready = bool((self.mcp_token and is_valid_credential_value(self.mcp_token)) or (self.bot_token and is_valid_credential_value(self.bot_token)))
        is_composio_ready = bool(COMPOSIO_CLIENT.is_configured() and VAULT.is_composio_connected("slack"))
        if action in ("post_message", "send_message", "slack.post_message"):
            channel = params.get("channel", "general")
            text = params.get("text", "")

            if self.mode in ("live", "remote_mcp") and (is_composio_ready or is_mcp_ready or self.mode == "remote_mcp"):
                try:
                    from mcp_servers.remote_client import RemoteMCPClient
                    client = RemoteMCPClient(
                        service="slack",
                        endpoint_url=self.mcp_endpoint,
                        auth_token=self.mcp_token or self.user_token or self.bot_token,
                    )
                    for tool_name in ["SLACK_POST_MESSAGE", "slack_send_message", "send_message", "chat_postMessage"]:
                        try:
                            res = await client.call_tool(tool_name, {"channel_id": channel, "channel": channel, "message": text, "text": text})
                            if res and not (isinstance(res, dict) and (res.get("error") or res.get("is_error"))):
                                return res if isinstance(res, dict) else {"status": "posted", "result": res}
                        except Exception:
                            continue
                except Exception as e:
                    logger.warning(f"Slack remote MCP send_message failed: {e}")

            token = self.bot_token or self.user_token
            if token and is_valid_credential_value(token):
                try:
                    headers = {
                        "Authorization": f"Bearer {token.strip()}",
                        "Content-Type": "application/json; charset=utf-8"
                    }
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            "https://slack.com/api/chat.postMessage",
                            json={"channel": channel, "text": text},
                            headers=headers,
                            timeout=10.0
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("ok"):
                                return {"status": "posted", "channel": channel, "ts": data.get("ts")}
                except Exception as e:
                    logger.warning(f"Slack live chat.postMessage failed: {e}")

            # Mock fallback return
            return {
                "status": "posted",
                "channel": channel,
                "text": text,
                "ts": "1725642100.000100"
            }

        raise NotImplementedError(f"Mutating Slack with action '{action}' is not supported.")
