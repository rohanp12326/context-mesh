"""API endpoint integration tests."""

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock
from apps.api.main import app
from agent.state import AgentResponse, QueryPlan, Citation


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["connector_mode"] == "live"
        assert "ZAI GLM" in data["llm_provider"]


@pytest.mark.asyncio
async def test_chat_endpoint(monkeypatch):
    import apps.api.routes as routes

    mock_resp = AgentResponse(
        answer="Priya committed to delivering auth fixes by Friday.",
        citations=[
            Citation(
                citation_id="cit-1",
                evidence_id="ev-1",
                claim="Priya delivery commitment",
                source_url="https://mail.google.com",
                source_type="gmail"
            )
        ],
        confidence=0.95,
        plan=QueryPlan(user_intent="inquiry", steps=[], risk_level="low")
    )
    monkeypatch.setattr(routes.agent_instance, "run", AsyncMock(return_value=mock_resp))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "query": "What did Priya commit to completing this week?",
            "user_id": "test_user",
            "thread_id": "test_thread"
        }
        resp = await client.post("/api/v1/chat", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "Priya committed" in data["answer"]
        assert len(data.get("citations", [])) == 1


@pytest.mark.asyncio
async def test_chat_endpoint_jit_auth_challenge(monkeypatch):
    import apps.api.routes as routes

    mock_resp = AgentResponse(
        answer="🔐 **Authentication Required**: This query needs data from **GMAIL**.",
        citations=[],
        confidence=1.0,
        plan=QueryPlan(user_intent="inquiry", steps=[], risk_level="low"),
        auth_required=True,
        missing_services=["gmail"],
        auth_challenge={"missing_services": ["gmail"], "connected_services": []}
    )
    monkeypatch.setattr(routes.agent_instance, "run", AsyncMock(return_value=mock_resp))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {
            "query": "What did Priya commit to completing this week?",
            "user_id": "test_user",
            "thread_id": "test_thread",
            "allow_auth_gate": True
        }
        resp = await client.post("/api/v1/chat", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("auth_required") is True
        assert "gmail" in data.get("missing_services", [])


@pytest.mark.asyncio
async def test_memory_endpoints():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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

        # List memories includes the new one
        resp = await client.get("/api/v1/memories")
        assert resp.status_code == 200
        mems = resp.json()
        assert len(mems) > 0
        assert any(m["content"].get("alias") == "ProjectX" for m in mems)

        # Delete memory
        del_resp = await client.delete(f"/api/v1/memories/{created['memory_id']}")
        assert del_resp.status_code == 200


@pytest.mark.asyncio
async def test_integration_endpoints(monkeypatch):
    from security.vault import VAULT
    monkeypatch.setattr(VAULT, "set_credential", lambda service, creds: None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Get status
        resp = await client.get("/api/v1/integrations/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "zai" in data["services"]

        # Configure service
        config_payload = {
            "service": "jira",
            "credentials": {"api_token": "test_token_123"}
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
