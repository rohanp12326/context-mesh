"""Streamlit Dashboard for ContextMesh - Enterprise AI Engineering Intelligence Assistant."""

import asyncio
import os
import json
import sys
from pathlib import Path

# Ensure project root is in sys.path regardless of execution directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from agent.graph import ContextMeshAgent
from connectors.base import PermissionScope
from security.vault import VAULT, mask_secret
from apps.web.auth_wizard import render_auth_wizard
from observability.logging import setup_logging, get_logger, read_latest_logs, get_log_file_path

setup_logging()
logger = get_logger("apps.web")

st.set_page_config(
    page_title="ContextMesh | OrgMind",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize agent instance in session state
if "agent" not in st.session_state:
    st.session_state.agent = ContextMeshAgent()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_approval" not in st.session_state:
    st.session_state.pending_approval = None
if "last_response" not in st.session_state:
    st.session_state.last_response = None

# Sidebar Configuration
status_summary = VAULT.get_status()
current_mode = status_summary["mode"]
services_status = status_summary["services"]

with st.sidebar:
    st.title("⚙️ ContextMesh Config")
    st.markdown("**Enterprise AI Intelligence Assistant**")

    # Mode Badge
    if current_mode == "mock":
        st.warning("🧪 **Mode: Demo Sandbox**\n*(Project Atlas synthetic data)*")
    else:
        st.success("🚀 **Mode: Live Enterprise**\n*(Querying live APIs)*")

    st.markdown("---")
    st.markdown("### 🤖 Active LLM Engine")
    zai_info = services_status["zai"]
    if zai_info["is_configured"]:
        st.success(f"✅ **ZAI GLM ({zai_info['model']})**\nKey: `{zai_info['masked_key']}`")
    else:
        st.info("ℹ️ **ZAI GLM (Mock Fallback)**\nConfigure in Auth tab")

    st.markdown("---")
    st.markdown("### 🔌 Connected Systems")
    st.markdown(f"{'✅' if services_status['jira']['is_configured'] else '⚪'} **Jira MCP**: {'Connected' if services_status['jira']['is_configured'] else 'Demo Data'}")
    st.markdown(f"{'✅' if services_status['notion']['is_configured'] else '⚪'} **Notion MCP**: {'Connected' if services_status['notion']['is_configured'] else 'Demo Data'}")
    st.markdown(f"{'✅' if services_status['gmail']['is_configured'] else '⚪'} **Gmail MCP**: {'Connected' if services_status['gmail']['is_configured'] else 'Demo Data'}")

    st.markdown("---")
    st.markdown("### 🛡️ Safety & Governance")
    st.caption("• Human approval required for write mutations\n• Automatic PII & secret redaction\n• AES-128 credential encryption")

st.title("🔍 ContextMesh — Cross-Tool Intelligence")
st.caption("Decomposed query planning across Jira, Notion, and Gmail with tiered memory and evidence-backed citations.")

tab_chat, tab_auth, tab_memory, tab_trace, tab_logs = st.tabs([
    "💬 Intelligence Chat",
    "🔐 Integrations & Auth",
    "🧠 Durable Memory",
    "📊 Trace Inspector",
    "📜 System Logs"
])


with tab_auth:
    render_auth_wizard()

with tab_chat:
    # Example Prompts
    st.markdown("**Sample Scenarios:**")
    col1, col2, col3 = st.columns(3)
    preset_query = None
    if col1.button("🔒 Auth Release Blockers"):
        preset_query = "What is blocking the authentication release, who owns each blocker, and what commitments were made in email?"
    if col2.button("⚡ Payments Delay Conflict"):
        preset_query = "Why was the payments launch delayed and how does it conflict with the Notion specification?"
    if col3.button("📝 Action Item to Jira Task"):
        preset_query = "Create a proposed Jira task from the unresolved action item in Notion meeting notes."

    # Display past conversation
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "plan" in msg and msg["plan"]:
                with st.expander("📋 View Decomposed Query Plan", expanded=False):
                    st.json(msg["plan"])
            if "citations" in msg and msg["citations"]:
                with st.expander("🔗 Source Citations", expanded=False):
                    for cit in msg["citations"]:
                        source = cit.get("source_type", "") if isinstance(cit, dict) else getattr(cit, "source_type", "")
                        claim = cit.get("claim", "") if isinstance(cit, dict) else getattr(cit, "claim", "")
                        url = cit.get("source_url", "#") if isinstance(cit, dict) else getattr(cit, "source_url", "#")
                        st.markdown(f"- **[{source.upper()}]** [{claim}]({url})")

    # Chat input
    user_input = st.chat_input("Ask a cross-tool question across Jira, Notion, or Gmail...")
    if preset_query:
        user_input = preset_query

    if user_input:
        # Append user message
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Run agent
        with st.chat_message("assistant"):
            with st.spinner("Decomposing query, querying MCP servers, and resolving evidence..."):
                response = asyncio.run(st.session_state.agent.run(
                    query=user_input,
                    thread_id="streamlit_session",
                    can_mutate=False
                ))
                st.session_state.last_response = response

                # Display Decomposed Plan
                if response.plan:
                    st.markdown("##### 🧭 Decomposed Query Plan")
                    cols = st.columns(len(response.plan.steps) if response.plan.steps else 1)
                    for idx, step in enumerate(response.plan.steps):
                        with cols[idx % len(cols)]:
                            tool_name = step.tool.split(".")[0].upper()
                            st.info(f"**Step {idx+1}**: {tool_name}\n*{step.purpose}*")

                # Display Contradictions
                if response.contradictions:
                    st.warning("⚠️ **Contradictions / Stale Data Detected**")
                    for c in response.contradictions:
                        st.markdown(f"**{c.topic}**: {c.description}")

                # Display Answer
                st.markdown(response.answer)

                # Display Citations
                if response.citations:
                    st.markdown("##### 📌 Evidence Citations")
                    for cit in response.citations:
                        source = cit.get("source_type", "") if isinstance(cit, dict) else getattr(cit, "source_type", "")
                        claim = cit.get("claim", "") if isinstance(cit, dict) else getattr(cit, "claim", "")
                        url = cit.get("source_url", "#") if isinstance(cit, dict) else getattr(cit, "source_url", "#")
                        st.markdown(f"- 📎 `[{source.upper()}]` [{claim}]({url})")

                # Handle Approval Requirement
                if response.requires_approval and response.pending_mutation:
                    st.session_state.pending_approval = response.pending_mutation
                    st.error("🛑 **HUMAN APPROVAL REQUIRED BEFORE EXECUTION**")
                    st.json(response.pending_mutation)
                    c_app, c_rej = st.columns(2)
                    if c_app.button("✅ Approve Jira Issue Creation"):
                        scope = PermissionScope(allowed_scopes=["write:jira"], can_mutate=True)
                        res = asyncio.run(st.session_state.agent.tool_registry.jira.connector.mutate(
                            "create_issue", response.pending_mutation.get("params", {}), scope=scope
                        ))
                        st.success(f"Mutation Executed! Issue created: {res.get('issue_key')}")
                        st.session_state.pending_approval = None
                    if c_rej.button("❌ Reject Action"):
                        st.info("Action canceled.")
                        st.session_state.pending_approval = None

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response.answer,
                    "plan": response.plan.model_dump() if response.plan else None,
                    "citations": [c.model_dump() for c in response.citations]
                })

with tab_memory:
    st.subheader("🧠 Durable Cross-Session Memory")
    st.caption("Durable facts, project aliases, and role mappings preserved across separate sessions.")

    records = st.session_state.agent.long_term.list_records()
    for rec in records:
        with st.container():
            st.markdown(f"**Namespace**: `{'/'.join(rec.namespace)}` | **Type**: `{rec.type}`")
            st.json(rec.content)
            st.caption(f"Confidence: {rec.confidence} | Created: {rec.created_at}")
            st.divider()

    st.markdown("#### ➕ Add New Durable Knowledge")
    with st.form("add_memory_form"):
        alias_name = st.text_input("Project / Alias Name", value="PaymentsV2")
        jira_key = st.text_input("Associated Jira Project Key", value="PAY")
        submitted = st.form_submit_button("Save to Long-Term Memory")
        if submitted:
            rec = st.session_state.agent.promotion_engine.promote_alias_candidate(
                alias=alias_name,
                jira_project=jira_key,
                source_ref="streamlit_admin"
            )
            st.success(f"Saved memory {rec.memory_id} successfully!")
            st.rerun()

with tab_trace:
    st.subheader("📊 Hierarchical Execution Traces")
    st.caption("End-to-end request latency, tool invocations, and sanitized payloads.")
    if st.session_state.last_response and st.session_state.last_response.trace_id:
        from observability.tracing import GLOBAL_TRACER
        trace = GLOBAL_TRACER.get_trace(st.session_state.last_response.trace_id)
        if trace:
            st.markdown(f"**Trace ID**: `{trace.trace_id}`")
            st.markdown(f"**Total Request Latency**: `{trace.total_duration_ms or 0} ms`")
            st.json(trace.model_dump())
        else:
            st.info("No trace details found for this request.")
    else:
        st.info("Execute a query in the chat tab to inspect execution traces.")

with tab_logs:
    st.subheader("📜 Application Execution Logs")
    log_path = get_log_file_path()
    st.caption(f"Real-time logs persisted to `{log_path}` (with PII & credential redaction).")

    col_l1, col_l2, col_l3 = st.columns([2, 1, 1])
    with col_l1:
        max_lines = st.slider("Number of log lines to show", min_value=20, max_value=500, value=100, step=20)
    with col_l2:
        if st.button("🔄 Refresh Logs", key="btn_refresh_logs"):
            st.rerun()
    with col_l3:
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                log_data = lf.read()
            st.download_button(
                "💾 Download Log File",
                data=log_data,
                file_name="context_mesh.log",
                mime="text/plain",
                key="btn_download_logs"
            )

    log_contents = read_latest_logs(max_lines=max_lines)
    st.code(log_contents, language="log")

