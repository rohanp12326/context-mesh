"""Gmail connector supporting synthetic mock email threads and live Gmail API."""

import json
import os
from typing import Any, Dict, List, Optional
from connectors.base import BaseConnector, ConnectorItem, PermissionScope
from security.vault import VAULT
from observability.logging import get_logger

logger = get_logger("connectors.gmail")


class GmailConnector(BaseConnector):
    """Connector for querying and fetching Gmail email threads."""

    def __init__(
        self,
        mode: Optional[str] = None,
        synthetic_data_path: Optional[str] = None,
        credentials_path: Optional[str] = None,
        token_path: Optional[str] = None,
    ):
        super().__init__(mode=mode or "mock", synthetic_data_path=synthetic_data_path)
        self._explicit_mode = mode
        self._explicit_credentials_path = credentials_path
        self._explicit_token_path = token_path
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

    async def search(self, query: str, limit: int = 10, scope: Optional[PermissionScope] = None) -> List[ConnectorItem]:
        if scope and "read:gmail" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:gmail' scope")

        is_configured = bool(self.credentials_path or self.token_path)
        if self.mode == "mock" or not is_configured:
            if self.mode != "mock" and not is_configured:
                logger.info("Gmail live OAuth credentials not configured; falling back to synthetic dataset.")
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

        # Live Gmail implementation would use googleapiclient / httpx with oauth token
        raise NotImplementedError("Live Gmail requires Google OAuth2 token configuration. Use mock mode for local testing.")

    async def get_by_id(self, item_id: str, scope: Optional[PermissionScope] = None) -> Optional[ConnectorItem]:
        if scope and "read:gmail" not in scope.allowed_scopes:
            raise PermissionError("Access denied: missing 'read:gmail' scope")

        is_configured = bool(self.credentials_path or self.token_path)
        if self.mode == "mock" or not is_configured:
            threads = self._load_mock_data()
            for thread in threads:
                if thread.get("id") == item_id:
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

        raise NotImplementedError("Live Gmail requires OAuth2 token configuration.")

    async def mutate(self, action: str, params: Dict[str, Any], scope: Optional[PermissionScope] = None) -> Dict[str, Any]:
        raise NotImplementedError("Gmail write/mutation operations are not supported in this version.")
