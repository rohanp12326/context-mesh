"""Secure local credential vault using Fernet symmetric encryption."""

import json
import os
import stat
from typing import Any, Dict, Optional
from cryptography.fernet import Fernet


VAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".secrets")
KEY_FILE = os.path.join(VAULT_DIR, ".vault_key")
DATA_FILE = os.path.join(VAULT_DIR, "vault.enc")


DUMMY_CREDENTIAL_PATTERNS = {
    "your_jira_api_token",
    "secret_notion_api_token",
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

    def is_service_authenticated(self, service: str) -> bool:
        """Check if a service has valid, non-placeholder credentials configured."""
        creds = self.get_credential(service)
        if service == "jira":
            url = creds.get("base_url") or os.getenv("JIRA_BASE_URL", "")
            email = creds.get("user_email") or os.getenv("JIRA_USER_EMAIL", "")
            token = creds.get("api_token") or os.getenv("JIRA_API_TOKEN", "")
            return (
                is_valid_credential_value(url)
                and is_valid_credential_value(email)
                and is_valid_credential_value(token)
            )
        elif service == "notion":
            token = creds.get("api_key") or os.getenv("NOTION_API_KEY", "")
            return is_valid_credential_value(token)
        elif service == "gmail":
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
        notion_data = store.get("notion", {})
        gmail_data = store.get("gmail", {})

        zai_key = zai_data.get("api_key") or os.getenv("ZAI_API_KEY", "")
        jira_token = jira_data.get("api_token") or os.getenv("JIRA_API_TOKEN", "")
        notion_key = notion_data.get("api_key") or os.getenv("NOTION_API_KEY", "")

        return {
            "mode": mode,
            "services": {
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
                },
                "notion": {
                    "is_configured": self.is_service_authenticated("notion"),
                    "mode": self.get_service_mode("notion"),
                    "masked_token": mask_secret(notion_key),
                },
                "gmail": {
                    "is_configured": self.is_service_authenticated("gmail"),
                    "mode": self.get_service_mode("gmail"),
                    "account": gmail_data.get("account", os.getenv("GMAIL_ACCOUNT", "Not Connected")),
                    "auth_type": "app_password" if gmail_data.get("app_password") else ("oauth" if gmail_data.get("access_token") else "none"),
                }
            }
        }


# Global singleton vault instance
VAULT = CredentialVault()

