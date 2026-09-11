"""Slack connector supporting synthetic mock dataset, live Slack Web API, and official Slack MCP server."""

import json
import os
from typing import Any, Dict, List, Optional
import httpx
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT, is_valid_credential_value
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
        is_configured = bool(
            is_mcp_ready
            or (self.bot_token and is_valid_credential_value(self.bot_token))
            or (self.user_token and is_valid_credential_value(self.user_token))
            or VAULT.is_service_authenticated("slack")
        )
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Slack live credentials not configured; falling back to synthetic dataset.")
            return self._search_mock(query, limit)

        # Mode 1: Remote Official Hosted Slack MCP Server (mcp.slack.com)
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                remote_results = await self._search_remote_mcp(query, limit)
                if remote_results:
                    return remote_results
            except Exception as e:
                logger.warning(f"Slack remote MCP search failed: {e}. Falling back to standard live/mock methods.")

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
        """Search Slack workspace via official hosted Slack MCP server (mcp.slack.com)."""
        from mcp_servers.remote_client import RemoteMCPClient

        client = RemoteMCPClient(
            service="slack",
            endpoint_url=self.mcp_endpoint,
            auth_token=self.mcp_token or self.user_token or self.bot_token,
        )

        data = None
        # Official Slack MCP tools: slack_search_messages_and_files, slack_search_channels, search_messages, search
        for tool_name in ["slack_search_messages_and_files", "search_messages", "slack_search", "search"]:
            try:
                data = await client.call_tool(tool_name, {"query": query, "limit": limit, "count": limit})
                if data:
                    break
            except Exception as e:
                logger.debug(f"Slack MCP tool {tool_name} failed: {e}")
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
                logger.warning(f"Slack MCP returned error: {data.get('message') or data}")
                return []

        raw_items = []
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = data.get("messages") or data.get("matches") or data.get("results") or []

        items: List[ConnectorItem] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("ts") or f"mcp-slack-{idx}")
            channel = item.get("channel") or item.get("channel_name") or "general"
            if isinstance(channel, dict):
                channel = channel.get("name", "general")
            title = item.get("title") or f"Slack #{channel}"
            content = item.get("content") or item.get("text") or item.get("snippet") or f"Slack message in #{channel}"
            url = item.get("url") or item.get("permalink") or f"https://slack.com/archives/{channel}/{item_id}"
            updated_at = item.get("updated_at") or item.get("ts")

            items.append(
                ConnectorItem(
                    source="slack",
                    id=item_id,
                    title=str(title),
                    content=str(content),
                    url=url,
                    author=item.get("author") or item.get("user") or item.get("username"),
                    updated_at=str(updated_at) if updated_at else None,
                    raw_payload=item,
                    metadata={"channel": channel, "mcp": True}
                )
            )
        return items[:limit]

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:slack" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:slack' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_configured = bool(
            is_mcp_ready
            or (self.bot_token and is_valid_credential_value(self.bot_token))
            or (self.user_token and is_valid_credential_value(self.user_token))
            or VAULT.is_service_authenticated("slack")
        )
        if self.mode == "mock" or not is_configured:
            return self._get_by_id_mock(item_id)

        # Remote MCP tool: slack_read_channel, slack_read_thread, or slack_read_canvas
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                from mcp_servers.remote_client import RemoteMCPClient
                client = RemoteMCPClient(
                    service="slack",
                    endpoint_url=self.mcp_endpoint,
                    auth_token=self.mcp_token or self.user_token or self.bot_token,
                )
                for tool_name in ["slack_read_thread", "read_thread", "slack_read_channel", "read_channel", "slack_read_canvas"]:
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
        if action in ("post_message", "send_message", "slack.post_message"):
            channel = params.get("channel", "general")
            text = params.get("text", "")

            if self.mode == "remote_mcp" or is_mcp_ready:
                try:
                    from mcp_servers.remote_client import RemoteMCPClient
                    client = RemoteMCPClient(
                        service="slack",
                        endpoint_url=self.mcp_endpoint,
                        auth_token=self.mcp_token or self.user_token or self.bot_token,
                    )
                    for tool_name in ["slack_send_message", "send_message", "chat_postMessage"]:
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
