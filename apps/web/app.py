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

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import streamlit as st
from agent.graph import ContextMeshAgent
from connectors.base import PermissionScope
from security.vault import VAULT, mask_secret
from security.connection_testers import (
    test_jira_connection,
    test_slack_connection,
    test_gmail_connection
)
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

import inspect
import security.vault
import agent.state
import agent.graph

def reload_all_mesh_modules():
    """Purge and cleanly re-import all context-mesh modules to eliminate split-brain classes."""
    prefixes = (
        "agent",
        "connectors",
        "security",
        "retrieval",
        "memory",
        "observability",
        "mcp_servers",
        "apps.web.auth_wizard"
    )
    for mod in list(sys.modules.keys()):
        if any(mod == p or mod.startswith(p + ".") for p in prefixes):
            del sys.modules[mod]

def _vault():
    """Always return the current VAULT singleton from the security.vault module."""
    import security.vault
    return security.vault.VAULT

def _agent_response_is_fresh() -> bool:
    """Return True if the cached AgentResponse class has all required JIT auth fields."""
    try:
        import agent.state
        fields = agent.state.AgentResponse.model_fields
        return "auth_required" in fields and "missing_services" in fields and "skipped_services" in fields
    except Exception:
        return False

# Initialize or refresh agent instance in session state if method signature updated
_needs_reload = (
    "agent" not in st.session_state
    or not hasattr(st.session_state.agent, "run")
    or "allow_auth_gate" not in inspect.signature(st.session_state.agent.run).parameters
    or "skip_unauthenticated" not in inspect.signature(st.session_state.agent.run).parameters
    or not hasattr(_vault(), "get_missing_services")
    or not _agent_response_is_fresh()
)
if _needs_reload:
    reload_all_mesh_modules()
    import security.vault
    import agent.state
    import agent.graph
    st.session_state.agent = agent.graph.ContextMeshAgent()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_approval" not in st.session_state:
    st.session_state.pending_approval = None
if "pending_auth" not in st.session_state:
    st.session_state.pending_auth = None
if "last_response" not in st.session_state:
    st.session_state.last_response = None


def run_agent_async(**kwargs):
    """Run agent safely filtering kwargs against the active agent signature."""
    active_agent = st.session_state.agent
    sig = inspect.signature(active_agent.run).parameters
    call_kwargs = {k: v for k, v in kwargs.items() if k in sig}
    return asyncio.run(active_agent.run(**call_kwargs))


# Sidebar Configuration
status_summary = _vault().get_status()
current_mode = status_summary["mode"]
services_status = status_summary["services"]

with st.sidebar:
    st.title("⚙️ ContextMesh Config")
    st.markdown("**Enterprise AI Intelligence Assistant**")

    # Mode Badge
    if current_mode == "mock":
        st.warning("🧪 **Global Mode: Demo Sandbox**\n*(Synthetic Project Atlas)*")
    elif current_mode == "live":
        st.success("🚀 **Global Mode: Live Enterprise**\n*(Querying live APIs)*")
    else:
        st.info("⚡ **Global Mode: Smart Hybrid**\n*(Live where authenticated)*")

    st.markdown("---")
    st.markdown("### 🤖 Active LLM Engine")
    zai_info = services_status["zai"]
    if zai_info["is_configured"]:
        st.success(f"✅ **ZAI GLM ({zai_info['model']})**\nKey: `{zai_info['masked_key']}`")
    else:
        st.info("ℹ️ **ZAI GLM (Mock Fallback)**\nConfigure in Auth tab")

    st.markdown("---")
    st.markdown("### 🔌 Connected Systems")
    for svc_name in ["jira", "slack", "gmail"]:
        svc = services_status[svc_name]
        is_conn = svc["is_configured"]
        svc_mode = svc.get("mode", "mock")
        icon = "🟢" if is_conn else "⚪"
        mode_tag = "Live API" if (is_conn and svc_mode == "live") else "Demo Data"
        st.markdown(f"{icon} **{svc_name.upper()}**: {mode_tag}")

    st.markdown("---")
    st.markdown("### 🛡️ Safety & Governance")
    st.caption("• Just-In-Time app authentication\n• Human approval for write mutations\n• Automatic PII & secret redaction\n• AES-128 credential encryption")

    st.markdown("---")
    if st.button("🔄 Reload App Engine", help="Clears module cache and rebuilds agent cleanly", key="btn_reload_engine"):
        reload_all_mesh_modules()
        st.session_state.agent = None
        st.session_state.pending_auth = None
        st.session_state.pending_approval = None
        st.rerun()

st.title("🔍 ContextMesh — Cross-Tool Intelligence")
st.caption("Decomposed query planning across Jira, Slack, and Gmail with live data retrieval, tiered memory, and evidence citations.")

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
        preset_query = "Why was the payments launch delayed and how does it conflict with the Slack release specification?"
    if col3.button("📝 Action Item to Jira Task"):
        preset_query = "Create a proposed Jira task from the unresolved action item in Slack discussion."

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

    # Render Pending Authentication Challenge if active
    # Render Pending Authentication Challenge if active
    if st.session_state.pending_auth:
        challenge = st.session_state.pending_auth
        missing_svcs = challenge.get("missing_services", [])
        pending_q = challenge.get("query", "")
        required_svcs = challenge.get("required_services", [])
        connected_svcs = challenge.get("connected_services", [s for s in required_svcs if s not in missing_svcs])

        if connected_svcs:
            st.info(
                f"🟢 **Connected Services**: {', '.join(s.upper() for s in connected_svcs)} | "
                f"🔴 **Missing Auth**: {', '.join(s.upper() for s in missing_svcs)}\n\n"
                f"Your query involves **{', '.join(s.upper() for s in missing_svcs)}**, which is currently not authenticated. "
                f"You can authenticate below, or **proceed immediately** using your connected service(s) ({', '.join(s.upper() for s in connected_svcs)})."
            )
        else:
            st.warning(
                f"🔐 **Live App Authentication Required to Fetch Real Data**\n\n"
                f"Your query involves **{', '.join(s.upper() for s in missing_svcs)}**. "
                "To fetch your real organizational data instead of demo data, authenticate below:"
            )

        with st.container(border=True):
            tabs_auth = st.tabs([f"Connect {s.upper()}" for s in missing_svcs])

            for idx, svc in enumerate(missing_svcs):
                with tabs_auth[idx]:
                    if svc == "jira":
                        st.markdown("#### 📌 Connect Atlassian Jira")
                        st.link_button("🔗 Open Atlassian API Token Page", "https://id.atlassian.com/manage-profile/security/api-tokens", use_container_width=True)
                        st.info(
                            "**How to get your Jira API Token (30 seconds):**\n\n"
                            "1. Click the button above to open Atlassian Security.\n"
                            "2. Click **Create API token**, label it `ContextMesh`, and copy the token.\n"
                            "3. Enter your Jira URL (e.g. `https://your-company.atlassian.net`), email, and token below."
                        )
                        j_url = st.text_input("Jira URL", placeholder="https://your-company.atlassian.net", key="jit_jira_url")
                        j_email = st.text_input("User Email", placeholder="you@company.com", key="jit_jira_email")
                        j_token = st.text_input("Atlassian API Token", type="password", placeholder="ATATT3xFfGF0...", key="jit_jira_token")
                        if st.button("⚡ Test & Connect Jira", key="btn_jit_jira"):
                            with st.spinner("Verifying Jira connection..."):
                                ok, msg, _ = asyncio.run(test_jira_connection(j_url, j_email, j_token))
                                if ok:
                                    _vault().set_credential("jira", {"base_url": j_url, "user_email": j_email, "api_token": j_token})
                                    st.success(f"✅ {msg}")
                                    st.rerun()
                                else:
                                    st.error(f"❌ {msg}")

                    elif svc == "slack":
                        st.markdown("#### 💬 Connect Slack Integration")
                        st.link_button("🔗 Open Slack API Apps Console", "https://api.slack.com/apps", use_container_width=True)
                        st.info(
                            "**How to get your Slack Token (1 minute):**\n\n"
                            "1. Click the button above to open Slack's App console.\n"
                            "2. Create or select your `ContextMesh` app in your workspace.\n"
                            "3. Under **OAuth & Permissions**, copy your Bot User Token (`xoxb-...`) or User Token (`xoxp-...`)."
                        )
                        s_token = st.text_input("Slack Bot/User Token", type="password", placeholder="xoxb-... or xoxp-...", key="jit_slack_token")
                        if st.button("⚡ Test & Connect Slack", key="btn_jit_slack"):
                            with st.spinner("Verifying Slack connection..."):
                                ok, msg, _ = asyncio.run(test_slack_connection(s_token))
                                if ok:
                                    token_field = "user_token" if s_token.startswith("xoxp-") else "bot_token"
                                    _vault().set_credential("slack", {token_field: s_token.strip()})
                                    st.success(f"✅ {msg}")
                                    st.rerun()
                                else:
                                    st.error(f"❌ {msg}")

                    elif svc == "gmail":
                        st.markdown("#### 📧 Connect Gmail")
                        gm_auth_mode = st.radio("Gmail Auth Method", ["Google App Password (16 chars)", "OAuth2 Bearer Token"], horizontal=True, key="jit_gm_mode")

                        if "App Password" in gm_auth_mode:
                            st.link_button("🔗 Open Google App Passwords Page", "https://myaccount.google.com/apppasswords", use_container_width=True)
                            st.info(
                                "**How to get a Google App Password (Fastest & Easiest):**\n\n"
                                "1. Click **Open Google App Passwords Page** above.\n"
                                "2. If prompted, sign in with your Google account. *(Requires 2-Step Verification enabled)*.\n"
                                "3. Type an app name (e.g. `ContextMesh`) and click **Create**.\n"
                                "4. Copy the **16-letter password** (e.g. `xxxx xxxx xxxx xxxx`) and paste it below."
                            )
                            gm_email = st.text_input("Gmail Address", placeholder="you@company.com", key="jit_gm_email")
                            gm_pw = st.text_input("16-character App Password", type="password", placeholder="xxxx xxxx xxxx xxxx", key="jit_gm_pw")
                            gm_tok = None
                        else:
                            st.link_button("🔗 Open Google OAuth2 Playground", "https://developers.google.com/oauthplayground", use_container_width=True)
                            st.info(
                                "**For Technical Users / Developers:**\n\n"
                                "1. Click the button above to open Google OAuth2 Playground.\n"
                                "2. Select `https://mail.google.com/` scope, authorize, and exchange the code for tokens.\n"
                                "3. Copy the `ya29.a0...` Access Token and paste it below."
                            )
                            gm_email = st.text_input("Gmail Address", placeholder="you@company.com", key="jit_gm_email")
                            gm_tok = st.text_input("OAuth2 Access Token", type="password", placeholder="ya29.a0...", key="jit_gm_tok")
                            gm_pw = None

                        if st.button("⚡ Test & Connect Gmail", key="btn_jit_gmail"):
                            with st.spinner("Verifying Gmail connection..."):
                                ok, msg, _ = asyncio.run(test_gmail_connection(gm_email, gm_pw, gm_tok))
                                if ok:
                                    data = {"account": gm_email}
                                    if gm_pw:
                                        data["app_password"] = gm_pw
                                    if gm_tok:
                                        data["access_token"] = gm_tok
                                    _vault().set_credential("gmail", data)
                                    st.success(f"✅ {msg}")
                                    st.rerun()
                                else:
                                    st.error(f"❌ {msg}")

                    if connected_svcs:
                        st.markdown("---")
                        if st.button(f"⏩ Skip {svc.upper()} & Proceed with {', '.join(s.upper() for s in connected_svcs)}", key=f"btn_tab_skip_{svc}"):
                            with st.spinner(f"Proceeding with {', '.join(s.upper() for s in connected_svcs)} only..."):
                                response = run_agent_async(
                                    query=pending_q,
                                    thread_id="streamlit_session",
                                    can_mutate=False,
                                    skip_unauthenticated=True,
                                    allow_auth_gate=False
                                )
                                st.session_state.pending_auth = None
                                st.session_state.last_response = response
                                st.session_state.messages.append({
                                    "role": "assistant",
                                    "content": response.answer,
                                    "plan": response.plan.model_dump() if hasattr(response.plan, "model_dump") else response.plan,
                                    "citations": [c.model_dump() if hasattr(c, "model_dump") else c for c in response.citations]
                                })
                                st.rerun()

            st.markdown("---")
            col_act1, col_act2, col_act3 = st.columns([2, 2, 1])
            with col_act1:
                live_label = f"⏩ Proceed with {', '.join(s.upper() for s in connected_svcs)}" if connected_svcs else "🚀 Fetch Real Data Now"
                if st.button(live_label, type="primary", key="btn_exec_live"):
                    with st.spinner(f"Fetching live data from {'connected systems' if not connected_svcs else ', '.join(s.upper() for s in connected_svcs)}..."):
                        response = run_agent_async(
                            query=pending_q,
                            thread_id="streamlit_session",
                            can_mutate=False,
                            skip_unauthenticated=bool(connected_svcs),
                            allow_auth_gate=False
                        )
                        st.session_state.pending_auth = None
                        st.session_state.last_response = response
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": response.answer,
                            "plan": response.plan.model_dump() if hasattr(response.plan, "model_dump") else response.plan,
                            "citations": [c.model_dump() if hasattr(c, "model_dump") else c for c in response.citations]
                        })
                        st.rerun()

            with col_act2:
                if st.button("🧪 Proceed with Demo Data", key="btn_exec_demo"):
                    with st.spinner("Running query against synthetic Project Atlas demo data..."):
                        response = run_agent_async(
                            query=pending_q,
                            thread_id="streamlit_session",
                            can_mutate=False,
                            force_demo=True,
                            allow_auth_gate=False
                        )
                        st.session_state.pending_auth = None
                        st.session_state.last_response = response
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": response.answer,
                            "plan": response.plan.model_dump() if hasattr(response.plan, "model_dump") else response.plan,
                            "citations": [c.model_dump() if hasattr(c, "model_dump") else c for c in response.citations]
                        })
                        st.rerun()

            with col_act3:
                if st.button("❌ Cancel", key="btn_cancel_auth"):
                    if connected_svcs:
                        with st.spinner(f"Proceeding with {', '.join(s.upper() for s in connected_svcs)}..."):
                            response = run_agent_async(
                                query=pending_q,
                                thread_id="streamlit_session",
                                can_mutate=False,
                                skip_unauthenticated=True,
                                allow_auth_gate=False
                            )
                            st.session_state.pending_auth = None
                            st.session_state.last_response = response
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": response.answer,
                                "plan": response.plan.model_dump() if hasattr(response.plan, "model_dump") else response.plan,
                                "citations": [c.model_dump() if hasattr(c, "model_dump") else c for c in response.citations]
                            })
                            st.rerun()
                    else:
                        st.session_state.pending_auth = None
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"❌ **Query Canceled**: Operation canceled without authenticating {', '.join(s.upper() for s in missing_svcs)}.",
                            "plan": None,
                            "citations": []
                        })
                        st.rerun()

    # Render Pending Human Mutation Approval if active
    if st.session_state.pending_approval:
        mutation = st.session_state.pending_approval
        with st.container(border=True):
            st.error("🛑 **HUMAN APPROVAL REQUIRED BEFORE EXECUTION**")
            st.markdown(
                "A proposed write operation requires human confirmation in accordance with enterprise safety policies."
            )
            st.json(mutation)
            col_app1, col_app2 = st.columns(2)
            with col_app1:
                if st.button("✅ Approve & Execute Jira Issue Creation", type="primary", key="btn_approve_mutation"):
                    with st.spinner("Executing approved Jira mutation..."):
                        scope = PermissionScope(allowed_scopes=["write:jira"], can_mutate=True)
                        res = asyncio.run(st.session_state.agent.tool_registry.jira.connector.mutate(
                            "create_issue", mutation.get("params", {}), scope=scope
                        ))
                        issue_key = res.get("issue_key", "UNKNOWN")
                        succ_msg = f"✅ **Mutation Executed**: Jira issue **{issue_key}** created successfully! Status: {res.get('status', 'Open')}."
                        st.success(succ_msg)
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": succ_msg,
                            "plan": None,
                            "citations": []
                        })
                        st.session_state.pending_approval = None
                        st.rerun()

            with col_app2:
                if st.button("❌ Reject Action", key="btn_reject_mutation"):
                    rej_msg = "❌ **Action Rejected**: Proposed Jira mutation was canceled by the user."
                    st.info(rej_msg)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": rej_msg,
                        "plan": None,
                        "citations": []
                    })
                    st.session_state.pending_approval = None
                    st.rerun()

    # Chat input
    user_input = st.chat_input("Ask a cross-tool question across Jira, Slack, or Gmail...")
    if preset_query:
        user_input = preset_query

    if user_input:
        st.session_state.pending_auth = None
        st.session_state.pending_approval = None
        # Append user message
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        # Run agent
        with st.chat_message("assistant"):
            with st.spinner("Decomposing query and checking live application connections..."):
                response = run_agent_async(
                    query=user_input,
                    thread_id="streamlit_session",
                    can_mutate=False,
                    allow_auth_gate=True
                )

                st.session_state.last_response = response

                # If authentication is required for missing services, pause and trigger auth challenge
                if response.auth_required:
                    st.session_state.pending_auth = response.auth_challenge
                    st.rerun()

                # Display Decomposed Plan
                if response.plan:
                    st.markdown("##### 🧭 Decomposed Query Plan")
                    cols = st.columns(len(response.plan.steps) if response.plan.steps else 1)
                    for idx, step in enumerate(response.plan.steps):
                        with cols[idx % len(cols)]:
                            tool_name = step.tool.split(".")[0].upper()
                            st.info(f"**Step {idx+1}**: {tool_name}\n*{step.purpose}*")

                # Data Source Transparency Badge
                live_svcs = [
                    s for s in response.required_services
                    if _vault().is_service_authenticated(s) and _vault().get_service_mode(s) == "live"
                ]
                demo_svcs = [s for s in response.required_services if s not in live_svcs]
                if live_svcs:
                    st.success(f"🟢 **Live Data Retrieved From**: {', '.join(s.upper() for s in live_svcs)}")
                if getattr(response, "skipped_services", None):
                    st.info(f"⚪ **Skipped (Not Authenticated)**: {', '.join(s.upper() for s in response.skipped_services)}")
                if demo_svcs:
                    st.caption(f"🧪 **Demo Data Used For**: {', '.join(s.upper() for s in demo_svcs)}")

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

                # Append assistant response
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response.answer,
                    "plan": response.plan.model_dump() if hasattr(response.plan, "model_dump") else response.plan,
                    "citations": [c.model_dump() if hasattr(c, "model_dump") else c for c in response.citations]
                })

                # Handle Approval Requirement
                if response.requires_approval and response.pending_mutation:
                    st.session_state.pending_approval = response.pending_mutation
                    st.rerun()


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

