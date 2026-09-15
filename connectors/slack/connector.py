"""Slack connector supporting live Slack Web API and official Slack / Composio MCP server."""

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


def _extract_slack_author(item: Dict[str, Any], profile_cache: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """Extract human-readable author name from Slack message payload."""
    if not isinstance(item, dict):
        return "slack_user"

    # 1. Check user ID against cached user profiles
    user_id = item.get("user")
    if profile_cache and user_id and user_id in profile_cache:
        prof = profile_cache[user_id]
        name = prof.get("real_name") or prof.get("display_name") or prof.get("first_name")
        if name and isinstance(name, str) and name.strip():
            return name.strip()

    # 2. Direct author field
    author_direct = item.get("author")
    if author_direct and isinstance(author_direct, str) and author_direct.strip():
        return author_direct.strip()

    # 3. User profile object (standard in Slack search.messages when present)
    user_profile = item.get("user_profile")
    if isinstance(user_profile, dict):
        for key in ["real_name", "display_name", "name", "first_name"]:
            val = user_profile.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()

    # 4. Direct human name fields
    for key in ["real_name", "display_name", "user_name", "sender_name", "author_name"]:
        val = item.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # 5. Fallback to username handle
    username = item.get("username")
    if username and isinstance(username, str) and username.strip():
        return username.strip()

    # 6. Fallback to user ID or slack_user
    if user_id and isinstance(user_id, str) and user_id.strip():
        return user_id.strip()

    return "slack_user"


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
        super().__init__(mode=mode or "live", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_bot_token = bot_token
        self._explicit_user_token = user_token
        self._explicit_mcp_endpoint = mcp_endpoint
        self._explicit_mcp_token = mcp_token
        self._user_profile_cache: Dict[str, Dict[str, str]] = {}

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

    async def _resolve_user_profiles(self, user_ids: List[str], client: Any = None) -> None:
        """Fetch and cache user profile information for Slack user IDs."""
        missing = [u for u in set(user_ids) if u and isinstance(u, str) and u.startswith("U") and u not in self._user_profile_cache]
        if not missing:
            return

        import asyncio

        async def _fetch_one(uid: str):
            # Remote MCP / Composio
            if client:
                try:
                    res = await client.call_tool("SLACK_RETRIEVE_USER_PROFILE_INFORMATION", {"user": uid, "user_id": uid})
                    if isinstance(res, dict) and res.get("profile"):
                        prof = res["profile"]
                        real_name = prof.get("real_name") or prof.get("display_name") or prof.get("first_name") or ""
                        display_name = prof.get("display_name") or ""
                        first_name = prof.get("first_name") or ""
                        return uid, {
                            "real_name": str(real_name).strip(),
                            "display_name": str(display_name).strip(),
                            "first_name": str(first_name).strip(),
                        }
                except Exception as e:
                    logger.debug(f"Failed to fetch profile for {uid} via MCP: {e}")

            # Direct Web API
            active_token = self.user_token or self.bot_token
            if active_token and is_valid_credential_value(active_token):
                try:
                    headers = {"Authorization": f"Bearer {active_token.strip()}"}
                    async with httpx.AsyncClient() as http_client:
                        resp = await http_client.get(
                            "https://slack.com/api/users.profile.get",
                            params={"user": uid},
                            headers=headers,
                            timeout=8.0
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("ok") and data.get("profile"):
                                prof = data["profile"]
                                real_name = prof.get("real_name") or prof.get("display_name") or prof.get("first_name") or ""
                                display_name = prof.get("display_name") or ""
                                first_name = prof.get("first_name") or ""
                                return uid, {
                                    "real_name": str(real_name).strip(),
                                    "display_name": str(display_name).strip(),
                                    "first_name": str(first_name).strip(),
                                }
                except Exception as e:
                    logger.debug(f"Failed to fetch profile for {uid} via Web API: {e}")

            return uid, {}

        results = await asyncio.gather(*(_fetch_one(u) for u in missing), return_exceptions=True)
        for r in results:
            if isinstance(r, tuple):
                uid, info = r
                if info:
                    self._user_profile_cache[uid] = info

    def _extract_author(self, item: Dict[str, Any]) -> str:
        """Extract human-readable author name using cached user profiles."""
        return _extract_slack_author(item, self._user_profile_cache)

    @staticmethod
    def _extract_target_entity(query: str) -> Optional[str]:
        """Extract the target person/entity being inquired about, ignoring self-pronouns."""
        meta = {"me", "my", "mine", "all", "here", "channel", "everyone", "us"}

        # 1. from:<target> (where target != me)
        from_m = re.search(r"from:([a-zA-Z0-9_.-]+)", query, re.IGNORECASE)
        if from_m and from_m.group(1).lower() not in meta:
            return from_m.group(1).strip().lower()

        # 2. to:<target> (where target != me)
        to_m = re.search(r"to:([a-zA-Z0-9_.-]+)", query, re.IGNORECASE)
        if to_m and to_m.group(1).lower() not in meta:
            return to_m.group(1).strip().lower()

        # 3. with:<target> or with @<target>
        with_m = re.search(r"\bwith(?:\s+|:)(?:@)?([a-zA-Z0-9_.-]+)", query, re.IGNORECASE)
        if with_m and with_m.group(1).lower() not in meta:
            return with_m.group(1).strip().lower()

        # 4. Natural language from/by
        nl_from = re.search(r"\b(?:messages?\s+)?(?:from|by)\s+@?([a-zA-Z0-9_.-]+)", query, re.IGNORECASE)
        if nl_from and nl_from.group(1).lower() not in meta:
            return nl_from.group(1).strip().lower()

        # 5. Standalone entity
        clean = query.strip()
        if clean != "*" and len(clean.split()) <= 2 and not any(op in clean for op in [":", '"', "'"]):
            if clean.lower() not in meta:
                return clean.lower()

        return None

    @staticmethod
    def _sanitize_slack_query(query: str) -> str:
        """Normalize user query for Slack search API.

        Slack search API requires a non-empty string and performs literal keyword matching.
        1. If the query is empty or composed exclusively of recency/meta words, convert to '*' (wildcard).
        2. Preserves explicit Slack search syntax (e.g. 'from:@user', 'in:#channel').
        3. Simplifies conversational 'from:me to:<person>' -> '<person>' and 'from:<person> to:me' -> 'from:<person>'
           because Slack API does not support recipient channel filtering with to:me.
        4. Strips conversational stop-words while leaving the core entity/keyword clean.
        5. Does NOT inject invalid boolean operators like 'OR from:<name>', which break Slack's search parser.
        """
        if not query:
            return "*"
        clean = query.strip()
        if not clean or clean in ("*", '""', "''", "all"):
            return "*"

        # If query has from:me to:<person>, simplify to <person>
        m_from_me_to = re.search(r"from:me\s+to:([a-zA-Z0-9_.-]+)", clean, re.IGNORECASE)
        if m_from_me_to:
            return m_from_me_to.group(1).strip()

        # If query has from:<person> to:me, strip to:me
        m_from_to_me = re.search(r"from:([a-zA-Z0-9_.-]+)\s+to:me", clean, re.IGNORECASE)
        if m_from_to_me:
            return f"from:{m_from_to_me.group(1).strip()}"

        # If query is just to:me, convert to wildcard *
        if clean.lower() == "to:me":
            return "*"

        meta_words = {
            "what", "is", "are", "my", "mine", "the", "latest", "recent", "new", "newest",
            "slack", "messages", "message", "msg", "msgs", "chat", "chats",
            "show", "get", "fetch", "check", "find", "read", "list", "all",
            "inbox", "channel", "channels", "any", "does", "have", "has", "for", "me",
            "there", "tell", "about", "give", "display", "see", "conversation", "conversations"
        }

        words = re.findall(r"[a-zA-Z0-9_.-]+", clean.lower())
        if not words or all(w in meta_words for w in words):
            return "*"

        # If user/LLM already provided explicit Slack search operators, preserve as-is
        slack_operators = ["from:", "to:", "in:", "has:", "before:", "after:", "is:"]
        if any(op in clean.lower() for op in slack_operators):
            return clean

        # Natural language pattern: "from <target>" or "by <target>" or "messages from <target>"
        from_match = re.search(r"\b(?:messages?\s+)?(?:from|by)\s+@?([a-zA-Z0-9_.-]+)", clean, re.IGNORECASE)
        if from_match:
            target = from_match.group(1).strip()
            if target.lower() not in meta_words:
                return f"from:{target}"

        # Natural language pattern: "with <target>" or "conversation with <target>"
        with_match = re.search(r"\bwith(?:\s+|:)(?:@)?([a-zA-Z0-9_.-]+)", clean, re.IGNORECASE)
        if with_match:
            target = with_match.group(1).strip()
            if target.lower() not in meta_words:
                return target

        # Natural language pattern: "in channel <target>" or "in #<target>"
        in_match = re.search(r"\bin\s+(?:channel\s+)?#?([a-zA-Z0-9_.-]+)", clean, re.IGNORECASE)
        if in_match:
            target = in_match.group(1).strip()
            if target.lower() not in meta_words:
                return f"in:{target}"

        # Natural language pattern: "to <target>"
        to_match = re.search(r"\b(?:messages?\s+)?to\s+@?([a-zA-Z0-9_.-]+)", clean, re.IGNORECASE)
        if to_match:
            target = to_match.group(1).strip()
            if target.lower() == "me":
                return "*"
            if target.lower() not in meta_words:
                return f"to:{target}"

        # Conversational extraction: e.g. "are there any conversation of mine with monali" -> remaining non-meta words
        content_words = [w for w in re.findall(r"[a-zA-Z0-9_.-]+", clean) if w.lower() not in meta_words]
        if content_words:
            return " ".join(content_words)

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
        if not is_configured:
            logger.info("Slack live credentials not configured; returning empty list.")
            return []

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
                    clean_live_q = self._sanitize_slack_query(query)
                    resp = await client.get(
                        "https://slack.com/api/search.messages",
                        params={"query": clean_live_q, "count": limit},
                        headers=headers,
                        timeout=12.0
                    )
                    raw_matches = []
                    seen_ids = set()
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("ok"):
                            for m in data.get("messages", {}).get("matches", []):
                                tid = str(m.get("ts") or m.get("id") or "")
                                if tid and tid not in seen_ids:
                                    seen_ids.add(tid)
                                    raw_matches.append(m)

                    # Detect target author
                    target_author = self._extract_target_entity(clean_live_q) or self._extract_target_entity(query)

                    # Fallback for target author or 0 matches
                    if target_author or len(raw_matches) == 0:
                        wild_resp = await client.get(
                            "https://slack.com/api/search.messages",
                            params={"query": "*", "count": 20},
                            headers=headers,
                            timeout=12.0
                        )
                        if wild_resp.status_code == 200:
                            wdata = wild_resp.json()
                            if wdata.get("ok"):
                                for m in wdata.get("messages", {}).get("matches", []):
                                    tid = str(m.get("ts") or m.get("id") or "")
                                    if tid and tid not in seen_ids:
                                        seen_ids.add(tid)
                                        raw_matches.append(m)

                        if target_author and clean_live_q != target_author:
                            kw_resp = await client.get(
                                "https://slack.com/api/search.messages",
                                params={"query": target_author, "count": limit},
                                headers=headers,
                                timeout=12.0
                            )
                            if kw_resp.status_code == 200:
                                kdata = kw_resp.json()
                                if kdata.get("ok"):
                                    for m in kdata.get("messages", {}).get("matches", []):
                                        tid = str(m.get("ts") or m.get("id") or "")
                                        if tid and tid not in seen_ids:
                                            seen_ids.add(tid)
                                            raw_matches.append(m)

                    # Resolve profiles
                    uids = [m.get("user") for m in raw_matches if m.get("user") and isinstance(m.get("user"), str)]
                    await self._resolve_user_profiles(uids)

                    # Build items
                    authored = []
                    mentions = []
                    others = []
                    for m in raw_matches:
                        channel_info = m.get("channel", {})
                        channel_name = channel_info.get("name") if isinstance(channel_info, dict) else str(channel_info)
                        msg_text = m.get("text", "")
                        author = self._extract_author(m)
                        uname = str(m.get("username") or "")
                        ts = m.get("ts", "")
                        permalink = m.get("permalink", f"https://slack.com/archives/{channel_name}/p{ts.replace('.', '')}")

                        c_item = ConnectorItem(
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
                        if target_author:
                            if target_author in author.lower() or target_author in uname.lower():
                                authored.append(c_item)
                            elif target_author in msg_text.lower():
                                mentions.append(c_item)
                            else:
                                others.append(c_item)
                        else:
                            others.append(c_item)

                    if target_author:
                        combined = authored + mentions
                        if combined:
                            return combined[:limit]
                        return others[:limit]
                    if others:
                        return others[:limit]
            except Exception as e:
                logger.warning(f"Slack live search API call failed: {e}.")

        return []

    async def _search_remote_mcp(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search Slack workspace via Composio MCP or official hosted Slack MCP server."""
        from mcp_servers.remote_client import RemoteMCPClient

        client = RemoteMCPClient(
            service="slack",
            endpoint_url=self.mcp_endpoint,
            auth_token=self.mcp_token or self.user_token or self.bot_token,
        )

        clean_query = self._sanitize_slack_query(query)

        # Detect target person if query is seeking messages from/by someone or targeting an entity
        target_author = self._extract_target_entity(clean_query) or self._extract_target_entity(query)

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

        # Extract primary matches
        raw_matches = []
        seen_ids = set()

        def _collect_matches(payload: Any):
            if not payload:
                return
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    return
            items = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                msg_obj = payload.get("messages")
                if isinstance(msg_obj, dict):
                    items = msg_obj.get("matches") or msg_obj.get("items") or []
                elif isinstance(msg_obj, list):
                    items = msg_obj
                else:
                    items = payload.get("matches") or payload.get("results") or payload.get("items") or []
            for it in items:
                if isinstance(it, dict):
                    tid = str(it.get("ts") or it.get("id") or it.get("iid") or "")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        raw_matches.append(it)
                    elif not tid:
                        raw_matches.append(it)

        _collect_matches(data)

        # Fallback & expansion logic:
        # If target_author was specified, or if primary query returned 0 matches
        if target_author or len(raw_matches) == 0:
            # 1. Fetch recent messages with wildcard '*' to find unindexed or non-keyword responses
            try:
                wildcard_data = await client.call_tool("SLACK_SEARCH_MESSAGES", {"query": "*", "limit": 20, "count": 20})
                _collect_matches(wildcard_data)
            except Exception as e:
                logger.debug(f"Slack wildcard fallback failed: {e}")

            # 2. If target_author was detected, also query keyword search for target_author to catch mentions
            if target_author and clean_query != target_author:
                try:
                    kw_data = await client.call_tool("SLACK_SEARCH_MESSAGES", {"query": target_author, "limit": limit, "count": limit})
                    _collect_matches(kw_data)
                except Exception as e:
                    logger.debug(f"Slack keyword fallback for '{target_author}' failed: {e}")

        if not raw_matches and last_error:
            raise last_error

        # Resolve profiles for all distinct users
        uids = [m.get("user") for m in raw_matches if m.get("user") and isinstance(m.get("user"), str)]
        await self._resolve_user_profiles(uids, client=client)

        # Build ConnectorItem list with resolved author metadata
        authored_items: List[ConnectorItem] = []
        mention_items: List[ConnectorItem] = []
        other_items: List[ConnectorItem] = []

        for idx, item in enumerate(raw_matches):
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

            author = self._extract_author(item)
            uname = str(item.get("username") or "")

            c_item = ConnectorItem(
                source="slack",
                id=item_id,
                title=str(title),
                content=str(msg_text),
                url=url,
                author=author,
                created_at=iso_time,
                updated_at=iso_time,
                raw_payload=item,
                metadata={"channel": channel, "mcp": True}
            )

            if target_author:
                if target_author in author.lower() or target_author in uname.lower():
                    authored_items.append(c_item)
                elif target_author in msg_text.lower():
                    mention_items.append(c_item)
                else:
                    other_items.append(c_item)
            else:
                other_items.append(c_item)

        if target_author:
            # If target person was requested, prioritize messages authored by them!
            # Include mentions as well so the agent has full context of incoming vs outgoing.
            combined = authored_items + mention_items
            return combined[:limit]

        return other_items[:limit]

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
        if not is_configured:
            return None

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

        return None

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

            raise RuntimeError("Slack live credentials or MCP connection not configured to post messages.")

        raise NotImplementedError(f"Mutating Slack with action '{action}' is not supported.")
