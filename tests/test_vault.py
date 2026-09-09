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

