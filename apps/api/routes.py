"""API router defining endpoints for query answering, mutation approvals, memories, and traces."""

import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from apps.api.schemas import (
    ApprovalRequest,
    ChatRequest,
    HealthResponse,
    MemoryCreateRequest,
    IntegrationConfigureRequest,
    IntegrationTestRequest
)
from agent.graph import ContextMeshAgent
from agent.state import AgentResponse
from memory.long_term import MemoryRecord
from observability.tracing import GLOBAL_TRACER
from connectors.base import PermissionScope
from security.vault import VAULT
from security.connection_testers import (
    test_zai_connection,
    test_jira_connection,
    test_slack_connection,
    test_gmail_connection
)

router = APIRouter(prefix="/api/v1")
agent_instance = ContextMeshAgent()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    zai_creds = VAULT.get_credential("zai")
    model = zai_creds.get("model", os.getenv("ZAI_MODEL", "glm-4.5-air"))
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        connector_mode=os.getenv("CONNECTOR_MODE", "mock"),
        llm_provider=f"ZAI GLM API ({model})"
    )



@router.post("/chat", response_model=AgentResponse)
async def handle_chat(req: ChatRequest):
    """Run agentic retrieval pipeline for the user query."""
    try:
        response = await agent_instance.run(
            query=req.query,
            user_id=req.user_id,
            thread_id=req.thread_id,
            can_mutate=req.can_mutate,
            force_demo=req.force_demo,
            allow_auth_gate=req.allow_auth_gate
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/approval")
async def handle_approval(req: ApprovalRequest):
    """Execute or reject an approval-gated mutation action."""
    if not req.approved:
        agent_instance.short_term.clear_pending_approvals(req.thread_id)
        return {"status": "rejected", "message": "Mutation was rejected by human operator."}

    action = req.mutation.get("action")
    params = req.mutation.get("params", {})
    scope = PermissionScope(user_id="operator", allowed_scopes=["write:jira"], can_mutate=True)

    try:
        if action == "jira.create_issue":
            res = await agent_instance.tool_registry.jira.connector.mutate("create_issue", params, scope=scope)
            agent_instance.short_term.clear_pending_approvals(req.thread_id)
            return {"status": "executed", "result": res}

        raise HTTPException(status_code=400, detail=f"Unsupported mutation: {action}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memories", response_model=List[MemoryRecord])
async def list_memories(namespace: Optional[str] = None):
    """List durable long-term memory records."""
    return agent_instance.long_term.list_records(namespace_prefix=namespace)


@router.post("/memories", response_model=MemoryRecord)
async def create_memory(req: MemoryCreateRequest):
    """Create a new durable memory entry."""
    rec = MemoryRecord(
        namespace=req.namespace,
        type=req.type,  # type: ignore
        content=req.content,
        source={"kind": "api_direct", "reference": "admin"}
    )
    agent_instance.long_term.add_record(rec)
    return rec


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a durable memory record."""
    success = agent_instance.long_term.delete_record(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted", "memory_id": memory_id}


@router.get("/trace/{trace_id}")
async def get_trace(trace_id: str):
    """Retrieve execution trace by trace ID."""
    trace = GLOBAL_TRACER.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace.model_dump()


@router.get("/integrations/status")
async def get_integrations_status():
    """Return configured/connected status of third-party systems."""
    return VAULT.get_status()


@router.post("/integrations/configure")
async def configure_integration(req: IntegrationConfigureRequest):
    """Save credentials securely into encrypted vault."""
    if req.service not in ["zai", "jira", "slack", "gmail", "system_config"]:
        raise HTTPException(status_code=400, detail=f"Unsupported service '{req.service}'")
    VAULT.set_credential(req.service, req.credentials)
    return {"status": "saved", "service": req.service, "vault_status": VAULT.get_status()}


@router.post("/integrations/test")
async def test_integration(req: IntegrationTestRequest):
    """Test third-party connection without modifying vault."""
    creds = req.credentials
    if req.service == "zai":
        ok, msg, lat = await test_zai_connection(
            api_key=creds.get("api_key", ""),
            base_url=creds.get("base_url", "https://open.bigmodel.cn/api/paas/v4/"),
            model=creds.get("model", "glm-4.5-air")
        )
    elif req.service == "jira":
        ok, msg, lat = await test_jira_connection(
            base_url=creds.get("base_url", ""),
            user_email=creds.get("user_email", ""),
            api_token=creds.get("api_token", "")
        )
    elif req.service == "slack":
        token = creds.get("bot_token") or creds.get("user_token") or creds.get("api_key") or creds.get("mcp_token", "")
        ok, msg, lat = await test_slack_connection(token=token)
    elif req.service == "gmail":
        ok, msg, lat = await test_gmail_connection(
            account_email=creds.get("account", ""),
            app_password=creds.get("app_password", ""),
            access_token=creds.get("access_token", "")
        )
    else:

        raise HTTPException(status_code=400, detail=f"Unknown service '{req.service}'")

    return {"service": req.service, "success": ok, "message": msg, "latency_ms": lat}

