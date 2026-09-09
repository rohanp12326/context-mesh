"""Secure local credential vault using Fernet symmetric encryption."""

import json
import os
import stat
from typing import Any, Dict, Optional
from cryptography.fernet import Fernet


VAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".secrets")
KEY_FILE = os.path.join(VAULT_DIR, ".vault_key")
DATA_FILE = os.path.join(VAULT_DIR, "vault.enc")


def mask_secret(secret: Optional[str]) -> str:
    """Mask a sensitive API key or password for safe UI display."""
    if not secret:
        return "Not Set"
    if len(secret) <= 8:
        return "******"
    prefix = secret[:3]
    suffix = secret[-4:]
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
            self._write_all(store)
            return True
        return False

    def get_connector_mode(self) -> str:
        """Return 'live' or 'mock' (defaults to 'mock')."""
        store = self._read_all()
        return store.get("system_config", {}).get("connector_mode", os.getenv("CONNECTOR_MODE", "mock"))

    def set_connector_mode(self, mode: str):
        """Set execution mode ('live' or 'mock')."""
        store = self._read_all()
        sys_conf = store.setdefault("system_config", {})
        sys_conf["connector_mode"] = mode
        self._write_all(store)

    def get_status(self) -> Dict[str, Any]:
        """Return sanitized connection overview for dashboard rendering."""
        store = self._read_all()
        mode = self.get_connector_mode()

        zai_data = store.get("zai", {})
        jira_data = store.get("jira", {})
        notion_data = store.get("notion", {})
        gmail_data = store.get("gmail", {})

        return {
            "mode": mode,
            "services": {
                "zai": {
                    "is_configured": bool(zai_data.get("api_key") or os.getenv("ZAI_API_KEY")),
                    "model": zai_data.get("model", os.getenv("ZAI_MODEL", "glm-4.5-air")),
                    "base_url": zai_data.get("base_url", os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")),
                    "masked_key": mask_secret(zai_data.get("api_key") or os.getenv("ZAI_API_KEY")),
                },
                "jira": {
                    "is_configured": bool(jira_data.get("api_token") or os.getenv("JIRA_API_TOKEN")),
                    "base_url": jira_data.get("base_url", os.getenv("JIRA_BASE_URL", "")),
                    "user_email": jira_data.get("user_email", os.getenv("JIRA_USER_EMAIL", "")),
                    "masked_token": mask_secret(jira_data.get("api_token") or os.getenv("JIRA_API_TOKEN")),
                },
                "notion": {
                    "is_configured": bool(notion_data.get("api_key") or os.getenv("NOTION_API_KEY")),
                    "masked_token": mask_secret(notion_data.get("api_key") or os.getenv("NOTION_API_KEY")),
                },
                "gmail": {
                    "is_configured": bool(gmail_data.get("credentials_file") or os.getenv("GMAIL_CREDENTIALS_FILE")),
                    "account": gmail_data.get("account", "Not Connected"),
                }
            }
        }


# Global singleton vault instance
VAULT = CredentialVault()
