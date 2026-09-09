"""API endpoint integration tests."""

import pytest
from httpx import ASGITransport, AsyncClient
from apps.api.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "ZAI GLM" in data["llm_provider"]


@pytest.mark.asyncio
async def test_chat_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "query": "What did Priya commit to completing this week?",
            "user_id": "test_user",
            "thread_id": "test_thread"
        }
        resp = await client.post("/api/v1/chat", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "answer" in data
        assert len(data.get("citations", [])) > 0


@pytest.mark.asyncio
async def test_memory_endpoints():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # List memories
        resp = await client.get("/api/v1/memories")
        assert resp.status_code == 200
        mems = resp.json()
        assert len(mems) > 0

        # Create memory
        new_mem = {
            "namespace": ["test", "project-x"],
            "type": "project_alias",
            "content": {"alias": "ProjectX", "jira_project": "PX"}
        }
        create_resp = await client.post("/api/v1/memories", json=new_mem)
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["content"]["alias"] == "ProjectX"

        # Delete memory
        del_resp = await client.delete(f"/api/v1/memories/{created['memory_id']}")
        assert del_resp.status_code == 200


@pytest.mark.asyncio
async def test_integration_endpoints():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Get status
        resp = await client.get("/api/v1/integrations/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "zai" in data["services"]

        # Configure service
        config_payload = {
            "service": "zai",
            "credentials": {"api_key": "test_zai_key_123", "model": "glm-4-plus"}
        }
        conf_resp = await client.post("/api/v1/integrations/configure", json=config_payload)
        assert conf_resp.status_code == 200
        assert conf_resp.json()["status"] == "saved"

        # Test integration
        test_payload = {
            "service": "zai",
            "credentials": {"api_key": ""}
        }
        test_resp = await client.post("/api/v1/integrations/test", json=test_payload)
        assert test_resp.status_code == 200
        assert test_resp.json()["success"] is False

        # Cleanup
        from security.vault import VAULT
        VAULT.delete_credential("zai")

