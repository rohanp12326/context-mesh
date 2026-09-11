"""Secure local credential vault using Fernet symmetric encryption."""

import json
import os
import stat
from typing import Any, Dict, Optional
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()


VAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".secrets")
KEY_FILE = os.path.join(VAULT_DIR, ".vault_key")
DATA_FILE = os.path.join(VAULT_DIR, "vault.enc")


DUMMY_CREDENTIAL_PATTERNS = {
    "your_jira_api_token",
    "xoxb-dummy-slack-token",
    "xoxp-dummy-slack-token",
    "your_slack_bot_token",
    "your_slack_user_token",
    "your_zai_api_key_here",
    "you@company.com",
    "https://your-domain.atlassian.net",
    "secrets/gmail_credentials.json",
    "secrets/gmail_token.json",
}


def is_valid_credential_value(val: Optional[str]) -> bool:
    """Return True if val is a non-empty, non-dummy credential."""
    if not val or not isinstance(val, str):
        return False
    clean = val.strip()
    if not clean:
        return False
    if clean.lower() in DUMMY_CREDENTIAL_PATTERNS:
        return False
    if clean.lower().startswith("your_"):
        return False
    return True


def mask_secret(secret: Optional[str]) -> str:
    """Mask a sensitive API key or password for safe UI display."""
    if not is_valid_credential_value(secret):
        return "Not Set"
    assert secret is not None
    clean = secret.strip()
    if len(clean) <= 8:
        return "******"
    prefix = clean[:3]
    suffix = clean[-4:]
    return f"{prefix}****...{suffix}"


class CredentialVault:
    """Secure encrypted storage for third-party API credentials."""

    def __init__(self, vault_dir: str = VAULT_DIR):
        self.vault_dir = vault_dir
        self.key_file = os.path.join(self.vault_dir, ".vault_key")
        self.data_file = os.path.join(self.vault_dir, "vault.enc")
        self._fernet = self._init_crypto()

    def _init_crypto(self) -> Fernet:
        """Initialize or generate master encryption key."""
        os.makedirs(self.vault_dir, exist_ok=True)
        try:
            os.chmod(self.vault_dir, stat.S_IRWXU)  # 0700: user only
        except Exception:
            pass

        if not os.path.exists(self.key_file):
            key = Fernet.generate_key()
            with open(self.key_file, "wb") as f:
                f.write(key)
            try:
                os.chmod(self.key_file, stat.S_IRUSR | stat.S_IWUSR)  # 0600: user rw only
            except Exception:
                pass
        else:
            with open(self.key_file, "rb") as f:
                key = f.read().strip()

        return Fernet(key)

    def _read_all(self) -> Dict[str, Any]:
        """Decrypt and load all credentials from storage."""
        if not os.path.exists(self.data_file):
            return {}
        try:
            with open(self.data_file, "rb") as f:
                encrypted_data = f.read()
            if not encrypted_data:
                return {}
            decrypted = self._fernet.decrypt(encrypted_data)
            return json.loads(decrypted.decode("utf-8"))
        except Exception:
            return {}

    def _write_all(self, data: Dict[str, Any]):
        """Encrypt and persist credentials to storage."""
        payload = json.dumps(data).encode("utf-8")
        encrypted = self._fernet.encrypt(payload)
        with open(self.data_file, "wb") as f:
            f.write(encrypted)
        try:
            os.chmod(self.data_file, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except Exception:
            pass

    def set_credential(self, service: str, data: Dict[str, Any]):
        """Save and encrypt credentials for a given service."""
        store = self._read_all()
        store[service] = data
        # If user explicitly sets credentials, automatically enable live mode for that service
        service_modes = store.setdefault("service_modes", {})
        service_modes[service] = "live"
        self._write_all(store)

    def get_credential(self, service: str) -> Dict[str, Any]:
        """Retrieve decrypted credentials for a given service."""
        store = self._read_all()
        return store.get(service, {})

    def delete_credential(self, service: str) -> bool:
        """Remove credentials for a given service."""
        store = self._read_all()
        if service in store:
            del store[service]
            service_modes = store.get("service_modes", {})
            if service in service_modes:
                del service_modes[service]
            self._write_all(store)
            return True
        return False

    def sync_composio_connections(self, active_accounts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, bool]:
        """Sync live connected account statuses from Composio to the local vault."""
        store = self._read_all()
        active_slugs = set()
        active_acc_map = {}
        if active_accounts is None:
            try:
                from mcp_servers.composio_client import ComposioMCPClient
                client = ComposioMCPClient()
                if client.is_configured():
                    accounts = client.list_connected_accounts_sync()
                    for a in accounts:
                        if str(a.get("status", "")).upper() == "ACTIVE":
                            tk = a.get("toolkit")
                            slug = tk.get("slug") if isinstance(tk, dict) else tk
                            if not slug:
                                app = a.get("app")
                                slug = app.get("slug") if isinstance(app, dict) else app
                            if slug:
                                s = str(slug).lower().strip()
                                active_slugs.add(s)
                                active_acc_map[s] = a.get("id") or a.get("nanoid")
            except Exception:
                pass
        else:
            for a in active_accounts:
                if str(a.get("status", "")).upper() == "ACTIVE":
                    tk = a.get("toolkit")
                    slug = tk.get("slug") if isinstance(tk, dict) else tk
                    if not slug:
                        app = a.get("app")
                        slug = app.get("slug") if isinstance(app, dict) else app
                    if slug:
                        s = str(slug).lower().strip()
                        active_slugs.add(s)
                        active_acc_map[s] = a.get("id") or a.get("nanoid")

        results = {}
        service_modes = store.setdefault("service_modes", {})
        for svc in ["jira", "slack", "gmail"]:
            creds = store.setdefault(svc, {})
            if svc in active_slugs:
                creds["composio_connected"] = True
                creds["auth_type"] = "composio"
                if svc in active_acc_map and active_acc_map[svc]:
                    creds["composio_account_id"] = str(active_acc_map[svc])
                service_modes[svc] = "live"
                results[svc] = True
            else:
                if creds.get("composio_connected") and active_slugs:
                    creds["composio_connected"] = False
                results[svc] = bool(creds.get("composio_connected"))
        self._write_all(store)
        return results

    def is_composio_connected(self, service: str) -> bool:
        """Check if a service is authenticated specifically via Composio."""
        creds = self.get_credential(service)
        composio_creds = self.get_credential("composio")
        composio_key = composio_creds.get("api_key") or os.getenv("COMPOSIO_API_KEY", "")
        if not is_valid_credential_value(composio_key):
            return False
        return bool(
            creds.get("composio_connected")
            or creds.get("composio_account_id")
            or creds.get("auth_type") == "composio"
        )

    def is_service_authenticated(self, service: str) -> bool:
        """Check if a service has valid, non-placeholder credentials configured."""
        creds = self.get_credential(service)
        composio_creds = self.get_credential("composio")
        composio_key = composio_creds.get("api_key") or os.getenv("COMPOSIO_API_KEY", "")
        has_composio = is_valid_credential_value(composio_key)

        if service == "composio":
            return has_composio

        if service == "jira":
            # Check Composio connected status
            if creds.get("composio_connected") or creds.get("composio_account_id"):
                return True
            if has_composio and creds.get("auth_type") == "composio":
                return True
            if is_valid_credential_value(creds.get("mcp_token") or os.getenv("ATLASSIAN_MCP_TOKEN", "")):
                return True
            url = creds.get("base_url") or os.getenv("JIRA_URL") or os.getenv("JIRA_BASE_URL", "")
            email = creds.get("user_email") or os.getenv("JIRA_USER_EMAIL", "")
            token = creds.get("api_token") or os.getenv("JIRA_API_TOKEN", "")
            return (
                is_valid_credential_value(url)
                and is_valid_credential_value(email)
                and is_valid_credential_value(token)
            )
        elif service == "slack":
            # Check Composio connected status
            if creds.get("composio_connected") or creds.get("composio_account_id"):
                return True
            if has_composio and creds.get("auth_type") == "composio":
                return True
            if is_valid_credential_value(creds.get("mcp_token") or os.getenv("SLACK_MCP_TOKEN", "")):
                return True
            token = creds.get("bot_token") or creds.get("user_token") or creds.get("api_key") or os.getenv("SLACK_BOT_TOKEN") or os.getenv("SLACK_USER_TOKEN") or os.getenv("SLACK_TOKEN", "")
            return is_valid_credential_value(token)
        elif service == "gmail":
            # Check Composio connected status
            if creds.get("composio_connected") or creds.get("composio_account_id"):
                return True
            if has_composio and creds.get("auth_type") == "composio":
                return True
            if is_valid_credential_value(creds.get("mcp_token") or os.getenv("GMAIL_MCP_TOKEN", "")):
                return True
            # Check for App Password mode or OAuth token mode
            app_pw = creds.get("app_password") or os.getenv("GMAIL_APP_PASSWORD", "")
            account = creds.get("account") or os.getenv("GMAIL_ACCOUNT", "")
            access_token = creds.get("access_token") or os.getenv("GMAIL_ACCESS_TOKEN", "")
            cred_file = creds.get("credentials_file") or os.getenv("GMAIL_CREDENTIALS_FILE", "")
            token_file = creds.get("token_file") or os.getenv("GMAIL_TOKEN_FILE", "")

            if is_valid_credential_value(account) and is_valid_credential_value(app_pw):
                return True
            if is_valid_credential_value(access_token):
                return True
            if is_valid_credential_value(cred_file) and os.path.exists(cred_file):
                return True
            if is_valid_credential_value(token_file) and os.path.exists(token_file):
                return True
            return False
        elif service == "zai":
            key = creds.get("api_key") or os.getenv("ZAI_API_KEY") or os.getenv("ZHIPUAI_API_KEY", "")
            return is_valid_credential_value(key)
        return False

    def get_connector_mode(self) -> str:
        """Return global mode 'live', 'mock', or 'auto' (defaults to 'mock')."""
        store = self._read_all()
        return store.get("system_config", {}).get("connector_mode", os.getenv("CONNECTOR_MODE", "mock"))

    def set_connector_mode(self, mode: str):
        """Set global execution mode ('live', 'mock', or 'auto')."""
        store = self._read_all()
        sys_conf = store.setdefault("system_config", {})
        sys_conf["connector_mode"] = mode
        self._write_all(store)

    def get_service_mode(self, service: str) -> str:
        """Determine whether an individual service should run in live or mock mode."""
        store = self._read_all()
        service_modes = store.get("service_modes", {})
        if service in service_modes:
            explicit = service_modes[service]
            if explicit == "live" and not self.is_service_authenticated(service):
                return "mock"
            return explicit

        global_mode = self.get_connector_mode()
        if global_mode == "mock":
            return "mock"
        elif global_mode == "live":
            # In live mode, only use live if authenticated, else fallback to mock
            return "live" if self.is_service_authenticated(service) else "mock"
        else:
            # Auto / hybrid mode: live if authenticated, mock if not
            return "live" if self.is_service_authenticated(service) else "mock"

    def set_service_mode(self, service: str, mode: str):
        """Set execution mode for an individual service."""
        store = self._read_all()
        service_modes = store.setdefault("service_modes", {})
        service_modes[service] = mode
        self._write_all(store)

    def get_missing_services(self, required_services: list[str]) -> list[str]:
        """Return list of required services that lack valid live authentication."""
        return [s for s in required_services if not self.is_service_authenticated(s)]

    def get_status(self) -> Dict[str, Any]:
        """Return sanitized connection overview for dashboard rendering."""
        store = self._read_all()
        mode = self.get_connector_mode()

        zai_data = store.get("zai", {})
        jira_data = store.get("jira", {})
        slack_data = store.get("slack", {})
        gmail_data = store.get("gmail", {})
        composio_data = store.get("composio", {})

        zai_key = zai_data.get("api_key") or os.getenv("ZAI_API_KEY", "")
        jira_token = jira_data.get("api_token") or os.getenv("JIRA_API_TOKEN", "")
        slack_token = slack_data.get("bot_token") or slack_data.get("user_token") or slack_data.get("api_key") or os.getenv("SLACK_BOT_TOKEN") or os.getenv("SLACK_USER_TOKEN") or os.getenv("SLACK_TOKEN", "")
        composio_key = composio_data.get("api_key") or os.getenv("COMPOSIO_API_KEY", "")

        return {
            "mode": mode,
            "services": {
                "composio": {
                    "is_configured": self.is_service_authenticated("composio"),
                    "mode": self.get_service_mode("composio"),
                    "user_id": composio_data.get("user_id", os.getenv("COMPOSIO_USER_ID", "default_user")),
                    "base_url": composio_data.get("base_url", os.getenv("COMPOSIO_BASE_URL", "https://backend.composio.dev/api/v3.1")),
                    "masked_key": mask_secret(composio_key),
                },
                "zai": {
                    "is_configured": self.is_service_authenticated("zai"),
                    "mode": self.get_service_mode("zai"),
                    "model": zai_data.get("model", os.getenv("ZAI_MODEL", "glm-4.5-air")),
                    "base_url": zai_data.get("base_url", os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")),
                    "masked_key": mask_secret(zai_key),
                },
                "jira": {
                    "is_configured": self.is_service_authenticated("jira"),
                    "mode": self.get_service_mode("jira"),
                    "base_url": jira_data.get("base_url", os.getenv("JIRA_BASE_URL", "")),
                    "user_email": jira_data.get("user_email", os.getenv("JIRA_USER_EMAIL", "")),
                    "masked_token": mask_secret(jira_token),
                    "auth_type": "composio" if (jira_data.get("composio_connected") or jira_data.get("auth_type") == "composio") else "token",
                    "composio_connected": bool(jira_data.get("composio_connected")),
                },
                "slack": {
                    "is_configured": self.is_service_authenticated("slack"),
                    "mode": self.get_service_mode("slack"),
                    "masked_token": mask_secret(slack_token),
                    "auth_type": "composio" if (slack_data.get("composio_connected") or slack_data.get("auth_type") == "composio") else "token",
                    "composio_connected": bool(slack_data.get("composio_connected")),
                },
                "gmail": {
                    "is_configured": self.is_service_authenticated("gmail"),
                    "mode": self.get_service_mode("gmail"),
                    "account": gmail_data.get("account", os.getenv("GMAIL_ACCOUNT", "Not Connected")),
                    "auth_type": "composio" if (gmail_data.get("composio_connected") or gmail_data.get("auth_type") == "composio") else ("app_password" if gmail_data.get("app_password") else ("oauth" if gmail_data.get("access_token") else "none")),
                    "composio_connected": bool(gmail_data.get("composio_connected")),
                }
            }
        }


# Global singleton vault instance
VAULT = CredentialVault()

