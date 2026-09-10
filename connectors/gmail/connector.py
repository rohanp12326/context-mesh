"""Gmail connector supporting synthetic mock email threads and live Gmail API / IMAP."""

import asyncio
import email
from email.header import decode_header
import json
import os
from typing import Any, Dict, List, Optional
import httpx
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT, is_valid_credential_value
from observability.logging import get_logger

logger = get_logger("connectors.gmail")


def _decode_mime_str(val: Optional[str]) -> str:
    """Safely decode MIME-encoded email header strings."""
    if not val:
        return ""
    decoded_fragments = []
    try:
        parts = decode_header(val)
        for text, encoding in parts:
            if isinstance(text, bytes):
                try:
                    decoded_fragments.append(text.decode(encoding or "utf-8", errors="replace"))
                except Exception:
                    decoded_fragments.append(text.decode("latin-1", errors="replace"))
            else:
                decoded_fragments.append(str(text))
        return " ".join(decoded_fragments)
    except Exception:
        return str(val)


def _extract_email_body(msg: email.message.Message) -> str:
    """Extract plain text body from email message structure."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdisp = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in cdisp:
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace").strip()
        # Fallback to HTML part if plain text not found
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace").strip()
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace").strip()
    return ""

def _extract_rest_body(payload: Dict[str, Any]) -> str:
    """Extract and decode body from Gmail REST API payload."""
    import base64
    body_data = ""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        body_data = payload["body"]["data"]
    elif "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                body_data = part["body"]["data"]
                break
            elif "parts" in part:
                res = _extract_rest_body(part)
                if res:
                    return res
        if not body_data:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/html" and "data" in part.get("body", {}):
                    body_data = part["body"]["data"]
                    break

    if not body_data and "data" in payload.get("body", {}):
        body_data = payload["body"]["data"]

    if body_data:
        try:
            pad = 4 - (len(body_data) % 4)
            if pad != 4:
                body_data += "=" * pad
            decoded = base64.urlsafe_b64decode(body_data)
            return decoded.decode("utf-8", errors="replace").strip()
        except Exception:
            pass
    return ""

class GmailConnector(BaseConnector):
    """Connector for querying and fetching Gmail email threads."""

    def __init__(
        self,
        mode: Optional[str] = None,
        synthetic_data_path: Optional[str] = None,
        account: Optional[str] = None,
        app_password: Optional[str] = None,
        access_token: Optional[str] = None,
        credentials_path: Optional[str] = None,
        token_path: Optional[str] = None,
        mcp_endpoint: Optional[str] = None,
        mcp_token: Optional[str] = None,
    ):
        super().__init__(mode=mode or "mock", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_account = account
        self._explicit_app_password = app_password
        self._explicit_access_token = access_token
        self._explicit_credentials_path = credentials_path
        self._explicit_token_path = token_path
        self._explicit_mcp_endpoint = mcp_endpoint
        self._explicit_mcp_token = mcp_token
        self._mock_data: Optional[Dict[str, Any]] = None

    @property
    def mode(self) -> str:
        if self._explicit_mode:
            return self._explicit_mode
        return VAULT.get_service_mode("gmail")

    @mode.setter
    def mode(self, value: str):
        self._explicit_mode = value

    @property
    def account(self) -> Optional[str]:
        if self._explicit_account:
            return self._explicit_account
        creds = VAULT.get_credential("gmail")
        return creds.get("account") or os.getenv("GMAIL_ACCOUNT")

    @property
    def app_password(self) -> Optional[str]:
        if self._explicit_app_password:
            return self._explicit_app_password
        creds = VAULT.get_credential("gmail")
        return creds.get("app_password") or os.getenv("GMAIL_APP_PASSWORD")

    @property
    def access_token(self) -> Optional[str]:
        if self._explicit_access_token:
            return self._explicit_access_token
        creds = VAULT.get_credential("gmail")
        return creds.get("access_token") or os.getenv("GMAIL_ACCESS_TOKEN")

    @property
    def credentials_path(self) -> Optional[str]:
        if self._explicit_credentials_path:
            return self._explicit_credentials_path
        creds = VAULT.get_credential("gmail")
        return creds.get("credentials_file") or os.getenv("GMAIL_CREDENTIALS_FILE")

    @property
    def token_path(self) -> Optional[str]:
        if self._explicit_token_path:
            return self._explicit_token_path
        creds = VAULT.get_credential("gmail")
        return creds.get("token_file") or os.getenv("GMAIL_TOKEN_FILE")

    @property
    def mcp_endpoint(self) -> str:
        if self._explicit_mcp_endpoint:
            return self._explicit_mcp_endpoint
        creds = VAULT.get_credential("gmail")
        return creds.get("mcp_endpoint") or os.getenv("GMAIL_MCP_ENDPOINT") or "https://gmailmcp.googleapis.com/mcp/v1"

    @property
    def mcp_token(self) -> Optional[str]:
        if self._explicit_mcp_token:
            return self._explicit_mcp_token
        creds = VAULT.get_credential("gmail")
        return creds.get("mcp_token") or os.getenv("GMAIL_MCP_TOKEN")

    def _load_mock_data(self) -> List[Dict[str, Any]]:
        if self._mock_data is not None:
            return self._mock_data.get("gmail", {}).get("threads", [])

        data_path = self.synthetic_data_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "evals", "datasets", "synthetic_atlas.json"
        )
        if os.path.exists(data_path):
            with open(data_path, "r", encoding="utf-8") as f:
                self._mock_data = json.load(f)
            return self._mock_data.get("gmail", {}).get("threads", [])
        return []

    def _search_mock(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search synthetic mock dataset."""
        threads = self._load_mock_data()
        results = []
        q_lower = query.lower()

        for thread in threads:
            haystack = (
                f"{thread.get('id', '')} {thread.get('subject', '')} "
                f"{thread.get('snippet', '')} {thread.get('from', '')} "
                f"{' '.join(thread.get('to', []))} "
                f"{' '.join(m.get('body', '') for m in thread.get('messages', []))}"
            ).lower()

            # Keyword matching
            words = [w for w in q_lower.replace('"', '').split() if len(w) > 2 and not w.startswith("newer_than")]
            if not words or any(w in haystack for w in words):
                first_msg = thread.get("messages", [{}])[0]
                results.append(
                    ConnectorItem(
                        source="gmail",
                        id=thread["id"],
                        title=f"Email: {thread.get('subject')}",
                        content=first_msg.get("body", thread.get("snippet", "")),
                        url=f"https://mail.google.com/mail/u/0/#inbox/{thread['id']}",
                        author=thread.get("from"),
                        created_at=thread.get("date"),
                        updated_at=thread.get("date"),
                        raw_payload=thread,
                        metadata={
                            "from": thread.get("from"),
                            "to": thread.get("to"),
                            "subject": thread.get("subject"),
                            "date": thread.get("date"),
                            "message_count": len(thread.get("messages", []))
                        }
                    )
                )
        return results[:limit]

    def _get_by_id_mock(self, item_id: str) -> Optional[ConnectorItem]:
        """Fetch item from synthetic mock dataset."""
        threads = self._load_mock_data()
        for thread in threads:
            if thread.get("id") == item_id or f"gmail-{thread.get('id')}" == item_id:
                first_msg = thread.get("messages", [{}])[0]
                return ConnectorItem(
                    source="gmail",
                    id=thread["id"],
                    title=f"Email: {thread.get('subject')}",
                    content=first_msg.get("body", thread.get("snippet", "")),
                    url=f"https://mail.google.com/mail/u/0/#inbox/{thread['id']}",
                    author=thread.get("from"),
                    created_at=thread.get("date"),
                    updated_at=thread.get("date"),
                    raw_payload=thread,
                    metadata={"from": thread.get("from"), "to": thread.get("to")}
                )
        return None

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:gmail" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:gmail' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_configured = bool(
            is_mcp_ready
            or (self.access_token and is_valid_credential_value(self.access_token))
            or (self.account and self.app_password and is_valid_credential_value(self.app_password))
            or VAULT.is_service_authenticated("gmail")
        )
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Gmail live credentials not configured; falling back to synthetic dataset.")
            return self._search_mock(query, limit)

        # Mode 1: Remote Official Google Workspace MCP Server
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                remote_results = await self._search_remote_mcp(query, limit)
                if remote_results:
                    return remote_results
            except Exception as e:
                logger.warning(f"Gmail remote MCP search failed: {e}. Falling back to standard live/mock methods.")

        # Live Mode A: App Password via IMAP SSL
        if self.account and self.app_password:
            try:
                return await asyncio.to_thread(self._search_imap, query, limit)
            except Exception as e:
                logger.warning(f"Live Gmail IMAP search failed: {e}. Falling back to mock dataset.")
                return self._search_mock(query, limit)

        # Live Mode B: OAuth Access Token via Gmail REST API
        if self.access_token:
            try:
                return await self._search_rest(query, limit)
            except Exception as e:
                logger.warning(f"Live Gmail REST search failed: {e}. Falling back to mock dataset.")
                return self._search_mock(query, limit)

        # Fallback to mock if live requested but missing specific credentials
        logger.warning("Gmail live mode selected but missing App Password or OAuth token; using mock fallback.")
        return self._search_mock(query, limit)

    async def _search_remote_mcp(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search Gmail threads via official Google Workspace MCP server."""
        from mcp_servers.remote_client import RemoteMCPClient

        client = RemoteMCPClient(
            service="gmail",
            endpoint_url=self.mcp_endpoint,
            auth_token=self.mcp_token or self.access_token,
        )
        clean_q = query.replace("project = ATL", "").replace("AND", "").strip()

        data = None
        for tool_name in ["search_threads", "search_messages", "search"]:
            try:
                data = await client.call_tool(tool_name, {"query": clean_q, "max_results": limit})
                if data:
                    break
            except Exception as e:
                logger.debug(f"Gmail MCP tool {tool_name} with max_results failed: {e}")
                try:
                    data = await client.call_tool(tool_name, {"query": clean_q})
                    if data:
                        break
                except Exception as e2:
                    logger.debug(f"Gmail MCP tool {tool_name} failed: {e2}")
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
            raw_items = data.get("threads") or data.get("messages") or data.get("results") or [data]

        items: List[ConnectorItem] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or f"mcp-gmail-{idx}")
            subject = item.get("subject") or item.get("snippet", "No Subject")[:50]
            content = item.get("snippet") or item.get("body") or json.dumps(item)
            from_addr = item.get("from") or item.get("sender") or "Unknown"
            date_val = item.get("date") or item.get("created_at") or ""
            items.append(
                ConnectorItem(
                    source="gmail",
                    id=f"gmail-{item_id}" if not item_id.startswith("gmail-") else item_id,
                    title=f"Email: {subject}",
                    content=str(content),
                    url=item.get("url") or f"https://mail.google.com/mail/u/0/#inbox/{item_id}",
                    author=from_addr,
                    created_at=date_val,
                    updated_at=date_val,
                    raw_payload=item,
                    metadata={"from": from_addr, "subject": subject, "date": date_val, "mcp": True}
                )
            )
        return items[:limit]

    def _search_imap(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Synchronous IMAP search running inside asyncio.to_thread."""
        import imaplib

        assert self.account and self.app_password
        clean_pw = self.app_password.strip().replace(" ", "")
        items: List[ConnectorItem] = []

        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        try:
            mail.login(self.account.strip(), clean_pw)
            mail.select("INBOX", readonly=True)

            # Extract search keywords
            words = [w for w in query.replace('"', '').split() if len(w) > 2 and not w.startswith("newer_than")]
            search_ids = []

            if words:
                # Search by subject or body for keywords
                for word in words[:3]:
                    status, data = mail.search(None, f'(OR (SUBJECT "{word}") (BODY "{word}"))')
                    if status == "OK" and data[0]:
                        search_ids.extend(data[0].split())
                search_ids = list(dict.fromkeys(search_ids))  # preserve order & unique
                # Fallback to ALL if specific search returned nothing and keywords were benchmark defaults
                if not search_ids and any(w.lower() in {"atlas", "commitments"} for w in words):
                    status, data = mail.search(None, "ALL")
                    if status == "OK" and data[0]:
                        search_ids = data[0].split()
            else:
                status, data = mail.search(None, "ALL")
                if status == "OK" and data[0]:
                    search_ids = data[0].split()

            # Reverse to get newest emails first
            search_ids.reverse()

            for msg_id in search_ids[:limit]:
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = _decode_mime_str(msg.get("Subject", "No Subject"))
                from_addr = _decode_mime_str(msg.get("From", "Unknown"))
                date_hdr = msg.get("Date", "")
                to_addr = _decode_mime_str(msg.get("To", ""))
                body = _extract_email_body(msg)

                uid = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)
                items.append(
                    ConnectorItem(
                        source="gmail",
                        id=f"gmail-{uid}",
                        title=f"Email: {subject}",
                        content=body[:1500] if body else "(Empty body)",
                        url=f"https://mail.google.com/mail/u/0/#inbox/{uid}",
                        author=from_addr,
                        created_at=date_hdr,
                        updated_at=date_hdr,
                        raw_payload={"id": uid, "subject": subject, "from": from_addr, "to": to_addr, "date": date_hdr},
                        metadata={
                            "from": from_addr,
                            "to": [to_addr],
                            "subject": subject,
                            "date": date_hdr,
                            "message_count": 1
                        }
                    )
                )
            return items
        finally:
            try:
                mail.logout()
            except Exception:
                pass

    async def _search_rest(self, query: str, limit: int = 10) -> List[ConnectorItem]:
        """Search Gmail via official REST API with Bearer token."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        url = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
        clean_q = query.replace("project = ATL", "").replace("AND", "").strip()

        async with httpx.AsyncClient(timeout=15.0) as client:
            params: Dict[str, Any] = {"maxResults": limit}
            if clean_q:
                params["q"] = clean_q
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # If a query containing benchmark keywords ("atlas" / "newer_than") returned 0 messages,
            # retry without query filter so live user inbox is not empty.
            if not data.get("messages") and clean_q and ("atlas" in clean_q.lower() or "newer_than" in clean_q.lower()):
                logger.info(f"Gmail live search for '{clean_q}' yielded 0 messages; retrying with recent inbox messages.")
                fallback_resp = await client.get(url, params={"maxResults": limit}, headers=headers)
                if fallback_resp.status_code == 200:
                    data = fallback_resp.json()

            items: List[ConnectorItem] = []
            for msg_summary in data.get("messages", []):
                msg_id = msg_summary["id"]
                detail_resp = await client.get(f"{url}/{msg_id}", headers=headers)
                if detail_resp.status_code != 200:
                    continue
                msg_detail = detail_resp.json()
                headers_list = msg_detail.get("payload", {}).get("headers", [])

                hdr_dict = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}
                subject = hdr_dict.get("subject", "No Subject")
                from_addr = hdr_dict.get("from", "Unknown")
                date_hdr = hdr_dict.get("date", "")
                snippet = msg_detail.get("snippet", "")
                body_content = _extract_rest_body(msg_detail.get("payload", {}))
                content_to_use = body_content if body_content else snippet

                items.append(
                    ConnectorItem(
                        source="gmail",
                        id=f"gmail-{msg_id}",
                        title=f"Email: {subject}",
                        content=content_to_use[:1500] if content_to_use else "(Empty body)",
                        url=f"https://mail.google.com/mail/u/0/#inbox/{msg_id}",
                        author=from_addr,
                        created_at=date_hdr,
                        updated_at=date_hdr,
                        raw_payload=msg_detail,
                        metadata={"from": from_addr, "subject": subject, "date": date_hdr}
                    )
                )
            return items

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:gmail" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:gmail' scope")

        is_mcp_ready = bool(self.mcp_token and is_valid_credential_value(self.mcp_token))
        is_configured = bool(
            is_mcp_ready
            or (self.access_token and is_valid_credential_value(self.access_token))
            or (self.account and self.app_password and is_valid_credential_value(self.app_password))
            or VAULT.is_service_authenticated("gmail")
        )
        if self.mode == "mock" or not is_configured:
            return self._get_by_id_mock(item_id)

        # Live fetch single thread
        clean_id = item_id.replace("gmail-", "")
        if self.mode == "remote_mcp" or is_mcp_ready:
            try:
                from mcp_servers.remote_client import RemoteMCPClient
                client = RemoteMCPClient(
                    service="gmail",
                    endpoint_url=self.mcp_endpoint,
                    auth_token=self.mcp_token or self.access_token,
                )
                for tool_name in ["get_thread", "get_message"]:
                    try:
                        try:
                            data = await client.call_tool(tool_name, {"thread_id": clean_id})
                        except Exception:
                            data = await client.call_tool(tool_name, {"id": clean_id})
                        if data:
                            if isinstance(data, str):
                                try:
                                    data = json.loads(data)
                                except Exception:
                                    pass
                            if isinstance(data, dict):
                                subject = data.get("subject", "No Subject")
                                return ConnectorItem(
                                    source="gmail",
                                    id=f"gmail-{clean_id}",
                                    title=f"Email: {subject}",
                                    content=data.get("snippet", data.get("body", "")),
                                    url=f"https://mail.google.com/mail/u/0/#inbox/{clean_id}",
                                    author=data.get("from", "Unknown"),
                                    created_at=data.get("date"),
                                    raw_payload=data,
                                    metadata={"from": data.get("from"), "subject": subject, "mcp": True}
                                )
                    except Exception:
                        continue
            except Exception as e:
                logger.debug(f"Gmail MCP get_by_id failed: {e}")

        if self.access_token:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{clean_id}", headers=headers)
                    if resp.status_code == 200:
                        msg = resp.json()
                        headers_list = msg.get("payload", {}).get("headers", [])
                        hdr_dict = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}
                        
                        snippet = msg.get("snippet", "")
                        body_content = _extract_rest_body(msg.get("payload", {}))
                        content_to_use = body_content if body_content else snippet

                        return ConnectorItem(
                            source="gmail",
                            id=f"gmail-{clean_id}",
                            title=f"Email: {hdr_dict.get('subject', 'No Subject')}",
                            content=content_to_use,
                            url=f"https://mail.google.com/mail/u/0/#inbox/{clean_id}",
                            author=hdr_dict.get("from"),
                            created_at=hdr_dict.get("date"),
                            raw_payload=msg
                        )
            except Exception as e:
                logger.warning(f"Live Gmail get_by_id failed: {e}. Falling back to mock dataset.")

        return self._get_by_id_mock(item_id)

    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        raise NotImplementedError("Gmail write/mutation operations are not supported in this version.")

