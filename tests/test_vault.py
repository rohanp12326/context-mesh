"""Unit tests for CredentialVault and connection testers."""

import os
import shutil
import tempfile
import pytest
from security.vault import CredentialVault, mask_secret
from security.connection_testers import (
    verify_zai_connection,
    verify_jira_connection,
    verify_notion_connection,
    verify_gmail_connection
)


@pytest.fixture
def temp_vault():
    temp_dir = tempfile.mkdtemp()
    vault = CredentialVault(vault_dir=temp_dir)
    yield vault
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_vault_encryption_roundtrip(temp_vault):
    sample_data = {
        "api_key": "test-key-12345678",
        "model": "glm-4-plus"
    }
    temp_vault.set_credential("zai", sample_data)

    # Verify decrypted data matches
    retrieved = temp_vault.get_credential("zai")
    assert retrieved == sample_data

    # Verify physical file is encrypted (does NOT contain raw plaintext key)
    with open(temp_vault.data_file, "rb") as f:
        raw_bytes = f.read()
    assert b"test-key-12345678" not in raw_bytes


def test_vault_mode_and_status(temp_vault):
    assert temp_vault.get_connector_mode() in ["mock", "live"]
    temp_vault.set_connector_mode("live")
    assert temp_vault.get_connector_mode() == "live"

    status = temp_vault.get_status()
    assert status["mode"] == "live"
    assert "zai" in status["services"]
    assert "jira" in status["services"]


def test_vault_deletion(temp_vault):
    temp_vault.set_credential("notion", {"api_key": "ntn_test123"})
    assert temp_vault.get_credential("notion").get("api_key") == "ntn_test123"

    deleted = temp_vault.delete_credential("notion")
    assert deleted is True
    assert temp_vault.get_credential("notion") == {}


def test_mask_secret():
    assert mask_secret("") == "Not Set"
    assert mask_secret(None) == "Not Set"
    assert mask_secret("short") == "******"
    masked = mask_secret("sk-abcdefgh12345678")
    assert masked.startswith("sk-")
    assert masked.endswith("5678")
    assert "abcdefgh" not in masked


@pytest.mark.asyncio
async def test_connection_testers_empty_inputs():
    ok, msg, _ = await verify_zai_connection("")
    assert ok is False
    assert "empty" in msg.lower()

    ok, msg, _ = await verify_jira_connection("", "", "")
    assert ok is False
    assert "required" in msg.lower()

    ok, msg, _ = await verify_notion_connection("")
    assert ok is False
    assert "empty" in msg.lower()

    ok, msg, _ = await verify_gmail_connection("invalid-email")
    assert ok is False


@pytest.mark.asyncio
async def test_verify_zai_connection_error_1211(monkeypatch):
    import httpx

    class MockResponse:
        status_code = 400
        text = '{"error":{"code":"1211","message":"模型不存在，请检查模型代码。"}}'

        def json(self):
            return {"error": {"code": "1211", "message": "模型不存在，请检查模型代码。"}}

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return MockResponse()

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    ok, msg, _ = await verify_zai_connection("dummy_key", model="glm-4-flash")
    assert ok is False
    assert "1211" in msg
    assert "glm-4.5-air" in msg


def test_is_valid_credential_value():
    from security.vault import is_valid_credential_value
    assert is_valid_credential_value("") is False
    assert is_valid_credential_value(None) is False
    assert is_valid_credential_value("your_jira_api_token") is False
    assert is_valid_credential_value("secret_notion_api_token") is False
    assert is_valid_credential_value("your_token_123") is False
    assert is_valid_credential_value("ATATT3xFfGF0realtoken") is True
    assert is_valid_credential_value("ntn_real_notion_key") is True


def test_is_service_authenticated_and_modes(temp_vault):
    # Initially not authenticated
    assert temp_vault.is_service_authenticated("jira") is False
    assert temp_vault.is_service_authenticated("notion") is False
    assert temp_vault.is_service_authenticated("gmail") is False

    # Set Jira credentials
    temp_vault.set_credential("jira", {
        "base_url": "https://company.atlassian.net",
        "user_email": "engineer@company.com",
        "api_token": "ATATT3xFfGF0realtoken"
    })
    assert temp_vault.is_service_authenticated("jira") is True
    assert temp_vault.get_service_mode("jira") == "live"

    # Notion still unauthenticated -> fallback to mock
    assert temp_vault.is_service_authenticated("notion") is False
    assert temp_vault.get_service_mode("notion") == "mock"

    # Missing services check
    missing = temp_vault.get_missing_services(["jira", "notion", "gmail"])
    assert "jira" not in missing
    assert "notion" in missing
    assert "gmail" in missing

    # Set Gmail with app password
    temp_vault.set_credential("gmail", {
        "account": "user@gmail.com",
        "app_password": "abcd efgh ijkl mnop"
    })
    assert temp_vault.is_service_authenticated("gmail") is True
    assert temp_vault.get_service_mode("gmail") == "live"
    missing2 = temp_vault.get_missing_services(["jira", "gmail"])
    assert len(missing2) == 0


