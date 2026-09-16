"""ContextMesh Web App - a conversational assistant for your team's tools.

The interface is deliberately simple: a clean, chat-first experience (think ChatGPT)
that anyone can use without knowing what an "MCP", "trace", or "namespace" is.
All the enterprise plumbing (integrations, memory, telemetry, logs) lives quietly
behind a single Settings panel in the sidebar.
"""

import asyncio
import inspect
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path regardless of execution directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

import streamlit as st

from apps.web.auth_wizard import render_auth_wizard
from connectors.base import PermissionScope
from observability.logging import get_log_file_path, get_logger, read_latest_logs, setup_logging
from security.connection_testers import (
    test_gmail_connection,
    test_jira_connection,
    test_slack_connection,
)

setup_logging()
logger = get_logger("apps.web")

# Page Configuration
st.set_page_config(
    page_title="ContextMesh",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded"
)

import agent.graph
import agent.state
import security.vault


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
    return security.vault.VAULT


def _agent_response_is_fresh() -> bool:
    """Return True if the cached AgentResponse class has all required JIT auth fields."""
    try:
        import agent.state
        fields = agent.state.AgentResponse.model_fields
        return "auth_required" in fields and "missing_services" in fields and "skipped_services" in fields
    except Exception:
        return False


def _get_codebase_version() -> float:
    """Return the latest mtime among python source files in key packages."""
    max_mtime = 0.0
    for folder in ("agent", "connectors", "retrieval", "security", "memory", "observability", "apps"):
        folder_path = PROJECT_ROOT / folder
        if folder_path.exists():
            for p in folder_path.rglob("*.py"):
                try:
                    mtime = p.stat().st_mtime
                    max_mtime = max(max_mtime, mtime)
                except OSError:
                    pass
    return max_mtime


current_code_version = _get_codebase_version()

# Initialize or refresh agent instance in session state if method signature or codebase updated
_needs_reload = (
    "agent" not in st.session_state
    or st.session_state.agent is None
    or not hasattr(st.session_state.agent, "run")
    or "allow_auth_gate" not in inspect.signature(st.session_state.agent.run).parameters
    or "skip_unauthenticated" not in inspect.signature(st.session_state.agent.run).parameters
    or "on_step" not in inspect.signature(st.session_state.agent.run).parameters
    or not hasattr(_vault(), "get_missing_services")
    or not _agent_response_is_fresh()
    or st.session_state.get("_code_version") != current_code_version
)
if _needs_reload:
    reload_all_mesh_modules()
    import agent.graph
    import agent.state
    st.session_state.agent = agent.graph.ContextMeshAgent()
    st.session_state._code_version = current_code_version


# ---------------------------------------------------------
# Session State
# ---------------------------------------------------------
if "conversations" not in st.session_state:
    first_id = uuid.uuid4().hex[:8]
    st.session_state.conversations = [
        {"id": first_id, "title": "New chat", "messages": []}
    ]
    st.session_state.active_id = first_id
if "active_id" not in st.session_state:
    st.session_state.active_id = st.session_state.conversations[0]["id"]
if "messages" not in st.session_state:
    active_for_init = next(
        (c for c in st.session_state.conversations if c["id"] == st.session_state.active_id),
        st.session_state.conversations[0],
    )
    st.session_state.messages = active_for_init["messages"]
if "pending_approval" not in st.session_state:
    st.session_state.pending_approval = None
if "pending_auth" not in st.session_state:
    st.session_state.pending_auth = None
if "last_response" not in st.session_state:
    st.session_state.last_response = None
if "dev_mode" not in st.session_state:
    st.session_state.dev_mode = False
if "preset_query" not in st.session_state:
    st.session_state.preset_query = None
if "pending_query" not in st.session_state:
    st.session_state.pending_query = None


def _get_conversation(conv_id: str) -> dict[str, Any] | None:
    for conv in st.session_state.conversations:
        if conv["id"] == conv_id:
            return conv
    return None


def _active_conversation() -> dict[str, Any]:
    conv = _get_conversation(st.session_state.active_id)
    if conv is None:
        conv = st.session_state.conversations[0]
        st.session_state.active_id = conv["id"]
    return conv


def start_new_conversation():
    """Create a fresh, empty conversation and make it active."""
    conv_id = uuid.uuid4().hex[:8]
    st.session_state.conversations.insert(0, {"id": conv_id, "title": "New chat", "messages": []})
    st.session_state.active_id = conv_id
    st.session_state.messages = st.session_state.conversations[0]["messages"]
    st.session_state.pending_approval = None
    st.session_state.pending_auth = None
    st.session_state.last_response = None
    st.session_state.preset_query = None
    st.session_state.pending_query = None


def switch_conversation(conv_id: str):
    """Make an existing conversation active."""
    conv = _get_conversation(conv_id)
    if conv is None:
        return
    st.session_state.active_id = conv_id
    st.session_state.messages = conv["messages"]
    st.session_state.pending_approval = None
    st.session_state.pending_auth = None
    st.session_state.last_response = None
    st.session_state.pending_query = None


def _retitle_active_conversation(first_user_message: str):
    conv = _active_conversation()
    if conv.get("title") in (None, "", "New chat"):
        title = first_user_message.strip().replace("\n", " ")
        conv["title"] = (title[:38] + "…") if len(title) > 38 else title or "New chat"


def run_agent_async(**kwargs):
    """Run agent safely filtering kwargs against the active agent signature."""
    active_agent = st.session_state.agent
    sig = inspect.signature(active_agent.run).parameters
    call_kwargs = {k: v for k, v in kwargs.items() if k in sig}
    return asyncio.run(active_agent.run(**call_kwargs))


# ---------------------------------------------------------
# Presentation helpers
# ---------------------------------------------------------
_EVIDENCE_REF = re.compile(r"\s*\[(?:gmail|slack|jira|web|notion|email|thread|evidence)[-_][0-9a-zA-Z\-]+\]")


def humanize_answer(text: str) -> str:
    """Strip raw evidence ids / machine tags from answers shown to non-technical users."""
    if not text:
        return ""
    return _EVIDENCE_REF.sub("", text)


def format_friendly_tool_action(tool_name: str, args: dict[str, Any]) -> str:
    """Translate technical tool signatures into clear human actions."""
    t_lower = tool_name.lower()
    if "jira.search" in t_lower or "jira_search" in t_lower:
        query_val = args.get("query", "")
        return f"🔍 Searching Jira tickets for: *\"{query_val[:50]}...\"*" if len(query_val) > 50 else f"🔍 Searching Jira tickets for: *\"{query_val}\"*"
    elif "jira.get_issue" in t_lower or "jira_get" in t_lower:
        key = args.get("issue_key", "ticket")
        return f"📌 Inspecting Jira issue **{key}**..."
    elif "jira.create_issue" in t_lower or "jira_create" in t_lower:
        summary = args.get("summary", "New issue")
        return f"📝 Preparing new Jira issue: *\"{summary}\"*"
    elif "slack.search" in t_lower or "slack_search" in t_lower:
        query_val = args.get("query", "")
        return f"💬 Searching Slack messages & channels for: *\"{query_val}\"*"
    elif "slack.get_thread" in t_lower or "slack_thread" in t_lower:
        return "💬 Reading Slack conversation thread context..."
    elif "gmail.search" in t_lower or "gmail_search" in t_lower:
        query_val = args.get("query", "")
        return f"📧 Searching Gmail emails for: *\"{query_val}\"*"
    elif "gmail.get_thread" in t_lower or "gmail_get" in t_lower:
        return "📧 Fetching email thread messages..."
    elif "web.search" in t_lower or "web_search" in t_lower or t_lower.startswith("web"):
        query_val = args.get("query", "")
        return f"🌐 Searching the web for: *\"{query_val}\"*"
    return f"🛠️ Gathering information from {tool_name}..."


def _badge_for_source(source: str) -> tuple:
    s = str(source).lower()
    if "jira" in s:
        return "badge-jira", "📌"
    if "slack" in s:
        return "badge-slack", "💬"
    if "web" in s:
        return "badge-web", "🌐"
    return "badge-gmail", "📧"


def render_citation_chips(citations: list[Any]):
    """Render evidence citations as clean, clickable pill chips."""
    if not citations:
        return

    chips_html = ["<div class='sources-row'>"]
    for cit in citations:
        source = cit.get("source_type", "") if isinstance(cit, dict) else getattr(cit, "source_type", "")
        claim = cit.get("claim", "") if isinstance(cit, dict) else getattr(cit, "claim", "")
        url = cit.get("source_url", "#") if isinstance(cit, dict) else getattr(cit, "source_url", "#")
        badge_class, icon = _badge_for_source(source)

        clean_claim = (claim[:52] + "…") if len(claim) > 52 else claim
        label = str(source).title() if source else "Source"
        chips_html.append(
            f"<a href='{url or '#'}' target='_blank' class='citation-chip'>"
            f"<span class='citation-badge {badge_class}'>{icon} {label}</span>"
            f"<span>{clean_claim}</span>"
            f"</a>"
        )
    chips_html.append("</div>")
    st.markdown("".join(chips_html), unsafe_allow_html=True)


def render_reasoning(msg: dict[str, Any]):
    """Render the agent's reasoning trail. Compact by default, detailed in Developer Mode."""
    react_steps = msg.get("react_steps", []) or []
    if not react_steps:
        return

    rounds_count = len(react_steps)
    label = f"Thought for {rounds_count} step{'s' if rounds_count > 1 else ''}"
    with st.expander(f"🧠 {label}", expanded=False):
        for step in react_steps:
            it_num = step.get("iteration", 1)
            thought = step.get("thought", "")
            tool_calls = step.get("tool_calls", [])
            observations = step.get("observations", [])

            st.markdown(f"**Step {it_num}**")
            if thought:
                st.caption(f"💭 {humanize_answer(thought)}")

            if st.session_state.dev_mode:
                for tc in tool_calls:
                    st.code(f"{tc.get('tool')}({json.dumps(tc.get('arguments', {}), indent=2)})", language="python")
                for obs in observations:
                    st.json(obs)
            else:
                for tc in tool_calls:
                    st.markdown(f"• {format_friendly_tool_action(tc.get('tool', ''), tc.get('arguments', {}) or {})}")
                for obs in observations:
                    if obs.get("success", True):
                        count = obs.get("items_count", 0)
                        st.caption(f"↳ {count} result{'s' if count != 1 else ''} found")
                    else:
                        st.caption(f"↳ ⚠️ {obs.get('error', 'Something went wrong')}")
            if it_num != react_steps[-1].get("iteration", 1):
                st.divider()


def render_message(msg: dict[str, Any]):
    """Render a single chat message with its supporting evidence."""
    role = msg.get("role", "assistant")
    is_user = role == "user"
    avatar = "🧑" if is_user else "✨"

    with st.chat_message(role, avatar=avatar):
        # Keep the reasoning trail above the answer, matching the live turn order.
        render_reasoning(msg)

        content = msg.get("content", "")
        if not is_user and not st.session_state.dev_mode:
            content = humanize_answer(content)
        st.markdown(content)

        if msg.get("contradictions"):
            st.markdown(
                """
                <div class="conflict-alert">
                    <div class="conflict-alert-title">⚠️ Conflicting information across your tools</div>
                </div>
                """,
                unsafe_allow_html=True
            )
            for c in msg["contradictions"]:
                topic = c.get("topic") if isinstance(c, dict) else getattr(c, "topic", "Conflict")
                desc = c.get("description") if isinstance(c, dict) else getattr(c, "description", "")
                st.markdown(f"- **{topic}**: {desc}")

        if msg.get("citations"):
            render_citation_chips(msg["citations"])

        if st.session_state.dev_mode and msg.get("plan"):
            with st.expander("📋 Query plan (developer view)", expanded=False):
                st.json(msg["plan"])


def scroll_chat_to_bottom():
    """Pin the conversation to the latest message (traditional chat behaviour)."""
    st.iframe(
        """
        <script>
        (function () {
            const doc = window.parent.document;
            const targets = [
                doc.querySelector('[data-testid="stMain"]'),
                doc.querySelector('[data-testid="stAppScrollToBottomContainer"]'),
                doc.querySelector('[data-testid="stAppViewContainer"]')
            ];
            for (const el of targets) {
                if (el) { try { el.scrollTop = el.scrollHeight; } catch (e) {} }
            }
        })();
        </script>
        """,
        height=1,
    )


def _assistant_message_dict(response) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.answer,
        "plan": response.plan.model_dump() if hasattr(response.plan, "model_dump") else response.plan,
        "citations": [c.model_dump() if hasattr(c, "model_dump") else c for c in response.citations],
        "react_steps": [s.model_dump() if hasattr(s, "model_dump") else s for s in response.react_steps],
        "contradictions": [c.model_dump() if hasattr(c, "model_dump") else c for c in response.contradictions] if response.contradictions else [],
    }


# ---------------------------------------------------------
# Styling - clean, consumer-grade chat look
# ---------------------------------------------------------
def inject_custom_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }

        /* Hide Streamlit chrome that confuses first-time users */
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
        [data-testid="stAppDeployButton"] { display: none; }
        [data-testid="stStatusWidget"] { display: none; }
        header[data-testid="stHeader"] { background: transparent; }

        /* Center the conversation like a chat app */
        .block-container {
            max-width: 820px;
            padding-top: 1.6rem;
            padding-bottom: 7rem;
        }

        /* Sidebar */
        section[data-testid="stSidebar"] {
            background: #f7f7f8;
            border-right: 1px solid #ececf1;
        }
        section[data-testid="stSidebar"] .block-container { padding-top: 1rem; }

        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 6px 2px 14px 2px;
        }
        .brand-logo {
            width: 34px; height: 34px;
            border-radius: 9px;
            background: #0f172a;
            color: #fff;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.05rem;
        }
        .brand-name { font-size: 1.02rem; font-weight: 700; color: #0f172a; line-height: 1.1; }
        .brand-sub { font-size: 0.74rem; color: #8e8ea0; }

        .side-label {
            font-size: 0.72rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #8e8ea0;
            margin: 14px 2px 6px 2px;
        }

        /* Sidebar buttons behave like chat list items */
        section[data-testid="stSidebar"] .stButton > button {
            text-align: left;
            justify-content: flex-start;
            border-radius: 10px;
            border: 1px solid transparent;
            background: transparent;
            color: #0f172a;
            font-weight: 500;
            padding: 8px 12px;
            transition: background 0.12s ease;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        section[data-testid="stSidebar"] .stButton > button:hover {
            background: #ececf1;
            border-color: transparent;
        }
        section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
            background: #ececf1;
            color: #0f172a;
            border-color: #d9d9e3;
            font-weight: 600;
        }

        /* New chat button */
        .st-key-new_chat_box .stButton > button {
            justify-content: center;
            border: 1px solid #d9d9e3;
            background: #ffffff;
            font-weight: 600;
            padding: 10px 12px;
        }
        .st-key-new_chat_box .stButton > button:hover { background: #f2f2f5; }

        /* Welcome / empty state */
        .welcome { text-align: center; margin: 8vh auto 26px auto; max-width: 640px; }
        .welcome h1 {
            font-size: 2rem; font-weight: 700; letter-spacing: -0.02em;
            color: #0f172a; margin-bottom: 10px;
        }
        .welcome p { font-size: 1rem; color: #6b6b80; line-height: 1.55; margin: 0; }

        /* Suggestion cards */
        .suggest-grid-title {
            font-size: 0.86rem; font-weight: 600; color: #6b6b80;
            margin: 10px 0 8px 2px;
        }

        /* Chat messages */
        [data-testid="stChatMessage"] {
            background: transparent;
            border: none;
            padding: 0.35rem 0;
        }
        [data-testid="stChatMessageContent"] { font-size: 0.97rem; line-height: 1.6; }

        /* Tool status pills */
        .status-pill {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 4px 12px; border-radius: 20px;
            font-size: 0.8rem; font-weight: 600; margin: 2px 4px;
            border: 1px solid rgba(148, 163, 184, 0.25);
        }
        .status-pill.connected { background: rgba(34, 197, 94, 0.1); color: #16a34a; border-color: rgba(34, 197, 94, 0.3); }
        .status-pill.disconnected { background: rgba(148, 163, 184, 0.1); color: #64748b; }

        /* Citations */
        .sources-row {
            display: flex; flex-wrap: wrap; gap: 8px;
            margin: 10px 0 4px 0;
            padding-top: 10px;
            border-top: 1px dashed #e6e6ee;
        }
        .citation-chip {
            display: inline-flex; align-items: center; gap: 6px;
            background: #f8fafc; border: 1px solid #e6e6ee;
            border-radius: 999px; padding: 4px 10px;
            font-size: 0.78rem; color: #3f3f50; text-decoration: none;
            transition: all 0.15s ease;
        }
        .citation-chip:hover { background: #eef2ff; border-color: #c7d2fe; color: #3730a3; }
        .citation-badge {
            font-size: 0.68rem; font-weight: 700;
            text-transform: uppercase; padding: 1px 6px; border-radius: 999px;
        }
        .badge-jira { background: #e0f2fe; color: #0369a1; }
        .badge-slack { background: #fce7f3; color: #be185d; }
        .badge-gmail { background: #fee2e2; color: #b91c1c; }
        .badge-web { background: #dcfce7; color: #15803d; }

        /* Conflict callout */
        .conflict-alert {
            background: #fff7ed; border-left: 4px solid #f97316;
            border-radius: 8px; padding: 10px 14px; margin: 12px 0 8px 0;
        }
        .conflict-alert-title { font-weight: 700; color: #c2410c; font-size: 0.9rem; }

        /* Action approval card */
        .action-card {
            background: #fffbeb; border: 1px solid #fde68a;
            border-radius: 14px; padding: 18px 20px; margin: 16px 0;
        }
        .action-card-header {
            font-size: 1.02rem; font-weight: 700; color: #92400e;
            display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
        }
        .action-card-desc { font-size: 0.9rem; color: #78350f; margin-bottom: 12px; }
        .action-field-table {
            width: 100%; background: #ffffff; border-radius: 8px;
            border: 1px solid #fef3c7; padding: 10px 14px;
            margin-bottom: 14px; font-size: 0.88rem;
        }

        /* Auth prompt */
        .auth-card {
            background: #eff6ff; border: 1px solid #bfdbfe;
            border-radius: 14px; padding: 18px 20px; margin: 16px 0;
        }
        .auth-card-title { font-size: 1.02rem; font-weight: 700; color: #1e40af; margin-bottom: 6px; }
        .auth-card-desc { font-size: 0.9rem; color: #1e3a8a; line-height: 1.5; }

        /* Chat input */
        [data-testid="stChatInput"] textarea { font-size: 0.97rem; }

        /* ChatGPT-style composer bar with inline model picker */
        .st-key-composer_box {
            border: 1px solid #d9d9e3;
            border-radius: 26px;
            background: #ffffff;
            padding: 6px 8px 6px 18px;
            box-shadow: 0 2px 10px rgba(15, 23, 42, 0.05);
            max-width: 820px;
            margin: 0 auto;
        }
        .st-key-composer_box [data-baseweb="input"],
        .st-key-composer_box [data-baseweb="base-input"] {
            border: none !important;
            background: transparent !important;
            box-shadow: none !important;
        }
        .st-key-composer_box [data-baseweb="select"] > div {
            border: none !important;
            background: transparent !important;
            box-shadow: none !important;
            font-size: 0.84rem;
            color: #6b6b80;
            min-height: 0;
        }
        .st-key-composer_box .stForm { border: none; }
        .st-key-composer_box [data-testid="stFormSubmitButton"] button {
            border-radius: 50%;
            width: 40px;
            height: 40px;
            padding: 0;
        }
        </style>
        """,
        unsafe_allow_html=True
    )


inject_custom_css()


# ---------------------------------------------------------
# Sidebar
# ---------------------------------------------------------
status_summary = _vault().get_status()
services_status = status_summary["services"]


def _is_service_on(svc_name: str) -> bool:
    svc = services_status.get(svc_name, {})
    return bool(svc.get("is_configured")) or _vault().is_service_authenticated(svc_name)


with st.sidebar:
    st.markdown(
        """
        <div class="brand">
            <div class="brand-logo">🤖</div>
            <div>
                <div class="brand-name">ContextMesh</div>
                <div class="brand-sub">Workplace AI assistant</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    with st.container(key="new_chat_box"):
        if st.button("＋  New chat", key="btn_new_chat", use_container_width=True):
            start_new_conversation()
            st.rerun()

    st.markdown('<div class="side-label">Recent chats</div>', unsafe_allow_html=True)
    for conv in st.session_state.conversations:
        is_active = conv["id"] == st.session_state.active_id
        if st.button(
            ("▸  " if is_active else "") + conv["title"],
            key=f"conv_{conv['id']}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ) and not is_active:
            switch_conversation(conv["id"])
            st.rerun()

    # Settings / everything technical, tucked away
    st.markdown('<div class="side-label">Setup & data</div>', unsafe_allow_html=True)
    with st.expander("⚙️ Settings", expanded=False):
        st.caption("Connect apps, review what the assistant remembers, or inspect system activity.")

        # Live connection summary
        conn_bits = []
        for svc_name, svc_label, svc_icon in [("jira", "Jira", "📌"), ("slack", "Slack", "💬"), ("gmail", "Gmail", "📧")]:
            on = _is_service_on(svc_name)
            conn_bits.append(
                f"<span class='status-pill {'connected' if on else 'disconnected'}'>"
                f"{svc_icon} {svc_label}: {'On' if on else 'Off'}</span>"
            )
        st.markdown("<div>" + "".join(conn_bits) + "</div>", unsafe_allow_html=True)

        if st.button("🔄 Refresh connections", key="btn_sidebar_sync", use_container_width=True):
            _vault().sync_composio_connections()
            st.rerun()

        st.toggle(
            "🛠️ Developer mode",
            key="dev_mode",
            help="Show raw tool arguments, query plans, and full execution details."
        )

        set_col, kb_col, act_col = st.tabs(["🔌 Apps", "🧠 Memory", "📊 Activity"])

        with set_col:
            render_auth_wizard()

        with kb_col:
            st.markdown("#### 🧠 What the assistant remembers")
            st.caption("Project aliases, role mappings, and confirmed team decisions kept across sessions.")
            records = st.session_state.agent.long_term.list_records()
            if records:
                for rec in records:
                    with st.container(border=True):
                        st.markdown(f"**{'/'.join(rec.namespace)}** · `{rec.type}`")
                        st.json(rec.content)
                        st.caption(f"Confidence: **{rec.confidence}** · Created: {str(rec.created_at)[:19]}")
            else:
                st.info("Nothing stored yet.")

            st.markdown("##### ➕ Add knowledge or a project alias")
            with st.form("add_memory_form"):
                col_m1, col_m2 = st.columns(2)
                with col_m1:
                    alias_name = st.text_input("Project / alias name", value="PaymentsV2", placeholder="e.g. PaymentsV2")
                with col_m2:
                    jira_key = st.text_input("Jira project key", value="PAY", placeholder="e.g. PAY")
                submitted = st.form_submit_button("Save", type="primary")
                if submitted:
                    rec = st.session_state.agent.promotion_engine.promote_alias_candidate(
                        alias=alias_name,
                        jira_project=jira_key,
                        source_ref="streamlit_admin"
                    )
                    st.success(f"Saved “{alias_name}” to memory.")
                    st.rerun()

        with act_col:
            st.markdown("#### 📊 Recent activity")
            st.caption("How long answers took and how many sources were used.")
            if st.session_state.last_response and st.session_state.last_response.trace_id:
                from observability.tracing import GLOBAL_TRACER
                trace = GLOBAL_TRACER.get_trace(st.session_state.last_response.trace_id)
                round_ct = len(st.session_state.last_response.react_steps) if st.session_state.last_response.react_steps else 1
                cit_ct = len(st.session_state.last_response.citations) if st.session_state.last_response.citations else 0
                m1, m2, m3 = st.columns(3)
                m1.metric("Time", f"{(trace.total_duration_ms or 0) / 1000:.1f}s" if trace else "—")
                m2.metric("Steps", round_ct)
                m3.metric("Sources", cit_ct)
                if trace and st.session_state.dev_mode:
                    st.json(trace.model_dump())
            else:
                st.info("Ask a question to see performance here.")

            st.markdown("##### 📜 System logs")
            log_path = get_log_file_path()
            l1, l2 = st.columns([2, 1])
            with l1:
                max_lines = st.slider("Lines", min_value=20, max_value=500, value=100, step=20, label_visibility="collapsed")
            with l2:
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                        log_data = lf.read()
                    st.download_button("💾", data=log_data, file_name="context_mesh.log", mime="text/plain", key="btn_download_logs", use_container_width=True)
            st.code(read_latest_logs(max_lines=max_lines), language="log")

        st.markdown("---")
        if st.button("🔄 Rebuild assistant engine", key="btn_reload_engine", use_container_width=True):
            reload_all_mesh_modules()
            st.session_state.agent = None
            st.session_state.pending_auth = None
            st.session_state.pending_approval = None
            st.rerun()


# ---------------------------------------------------------
# Main chat area
# ---------------------------------------------------------
def render_welcome():
    st.markdown(
        """
        <div class="welcome">
            <h1>How can I help you today?</h1>
            <p>Ask me anything across your team's Jira, Slack, and Gmail.
            I'll find the answer and show you where it came from.</p>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown('<div class="suggest-grid-title">Try one of these</div>', unsafe_allow_html=True)
    suggestions = [
        ("🚨", "Release blockers", "What is blocking the authentication release, who owns each blocker, and what commitments were made in email?"),
        ("⚖️", "Timeline & conflicts", "Why was the payments launch delayed and how does it conflict with the Slack release specification?"),
        ("📋", "Turn a Slack action item into a Jira task", "Create a proposed Jira task from the unresolved action item in Slack discussion."),
        ("🎯", "My urgent priorities", "What are my most urgent tasks across Jira and what recent discussions mention them?"),
    ]
    row1 = st.columns(2)
    row2 = st.columns(2)
    for idx, (icon, title, prompt) in enumerate(suggestions):
        col = row1[idx] if idx < 2 else row2[idx - 2]
        with col, st.container(border=True):
            st.markdown(f"**{icon} {title}**")
            st.caption(prompt)
            if st.button("Ask", key=f"card_q{idx}", use_container_width=True):
                st.session_state.preset_query = prompt
                st.rerun()


def render_auth_challenge():
    challenge = st.session_state.pending_auth
    if not challenge:
        return
    missing_svcs = challenge.get("missing_services", [])
    pending_q = challenge.get("query", "")
    required_svcs = challenge.get("required_services", [])
    connected_svcs = challenge.get("connected_services", [s for s in required_svcs if s not in missing_svcs])

    pretty = ", ".join(s.upper() for s in missing_svcs)
    st.markdown(
        f"""
        <div class="auth-card">
            <div class="auth-card-title">🔐 Connect {pretty} to continue</div>
            <div class="auth-card-desc">
                Your question needs live data from <strong>{pretty}</strong>.
                Connect in one click below and I'll pick up right where we left off.
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    with st.container(border=True):
        tabs_auth = st.tabs([f"Connect {s.upper()}" for s in missing_svcs])

        for idx, svc in enumerate(missing_svcs):
            with tabs_auth[idx]:
                st.caption("Secure official authorization in your browser — no passwords or tokens to copy.")
                col_jit_a, col_jit_b = st.columns(2)
                with col_jit_a:
                    if st.button(f"🔗 Get {svc.upper()} connect link", key=f"btn_jit_gen_{svc}", use_container_width=True):
                        from mcp_servers.composio_client import COMPOSIO_CLIENT
                        try:
                            url = asyncio.run(COMPOSIO_CLIENT.get_auth_url(svc))
                            st.session_state[f"jit_{svc}_url"] = url
                            st.success("Connect link ready!")
                        except Exception as e:
                            st.error(f"Error generating link: {e}")

                if st.session_state.get(f"jit_{svc}_url"):
                    st.link_button(f"👉 Authorize {svc.upper()} in browser", st.session_state[f"jit_{svc}_url"], type="primary", use_container_width=True)

                with col_jit_b:
                    if st.button(f"🔄 I've connected {svc.upper()}", key=f"btn_jit_check_{svc}", use_container_width=True):
                        from security.connection_testers import test_composio_app_connection
                        with st.spinner(f"Verifying {svc.upper()} connection..."):
                            ok, msg, _ = asyncio.run(test_composio_app_connection(svc))
                            if ok:
                                _vault().set_credential(svc, {"auth_type": "composio", "composio_connected": True})
                                st.success(f"✅ {msg}")
                                st.session_state.pending_auth = None
                                st.rerun()
                            else:
                                st.warning(f"⚠️ {msg}")

                with st.expander(f"Advanced: connect {svc.upper()} manually", expanded=False):
                    if svc == "jira":
                        j_url = st.text_input("Jira URL", placeholder="https://your-company.atlassian.net", key="jit_jira_url")
                        j_email = st.text_input("User email", placeholder="you@company.com", key="jit_jira_email")
                        j_token = st.text_input("Atlassian API token", type="password", placeholder="ATATT3xFfGF0...", key="jit_jira_token")
                        if st.button("Test & connect Jira", key="btn_jit_jira"):
                            with st.spinner("Verifying Jira connection..."):
                                ok, msg, _ = asyncio.run(test_jira_connection(j_url, j_email, j_token))
                                if ok:
                                    _vault().set_credential("jira", {"base_url": j_url, "user_email": j_email, "api_token": j_token, "auth_type": "token"})
                                    st.success(f"✅ {msg}")
                                    st.session_state.pending_auth = None
                                    st.rerun()
                                else:
                                    st.error(f"❌ {msg}")
                    elif svc == "slack":
                        s_token = st.text_input("Slack bot/user token", type="password", placeholder="xoxb-... or xoxp-...", key="jit_slack_token")
                        if st.button("Test & connect Slack", key="btn_jit_slack"):
                            with st.spinner("Verifying Slack connection..."):
                                ok, msg, _ = asyncio.run(test_slack_connection(s_token))
                                if ok:
                                    token_field = "user_token" if s_token.startswith("xoxp-") else "bot_token"
                                    _vault().set_credential("slack", {token_field: s_token.strip(), "auth_type": "token"})
                                    st.success(f"✅ {msg}")
                                    st.session_state.pending_auth = None
                                    st.rerun()
                                else:
                                    st.error(f"❌ {msg}")
                    elif svc == "gmail":
                        gm_email = st.text_input("Gmail address", placeholder="you@company.com", key="jit_gm_email")
                        gm_pw = st.text_input("16-character app password", type="password", placeholder="xxxx xxxx xxxx xxxx", key="jit_gm_pw")
                        if st.button("Test & connect Gmail", key="btn_jit_gmail"):
                            with st.spinner("Verifying Gmail connection..."):
                                ok, msg, _ = asyncio.run(test_gmail_connection(gm_email, gm_pw))
                                if ok:
                                    _vault().set_credential("gmail", {"account": gm_email, "app_password": gm_pw, "auth_type": "app_password"})
                                    st.success(f"✅ {msg}")
                                    st.session_state.pending_auth = None
                                    st.rerun()
                                else:
                                    st.error(f"❌ {msg}")

                if connected_svcs and st.button(f"Continue with {', '.join(s.upper() for s in connected_svcs)}", key=f"btn_tab_skip_{svc}", use_container_width=True):
                        with st.spinner("Finishing your answer..."):
                            response = run_agent_async(
                                query=pending_q,
                                thread_id=f"conv_{st.session_state.active_id}",
                                can_mutate=False,
                                skip_unauthenticated=True,
                                allow_auth_gate=False
                            )
                            st.session_state.pending_auth = None
                            st.session_state.last_response = response
                            st.session_state.messages.append(_assistant_message_dict(response))
                            st.rerun()

        col_act1, col_act2 = st.columns([3, 1])
        with col_act1:
            live_label = f"Continue with {', '.join(s.upper() for s in connected_svcs)}" if connected_svcs else "🚀 Run my question"
            if st.button(live_label, type="primary", key="btn_exec_live", use_container_width=True):
                with st.spinner("Fetching your answer..."):
                    response = run_agent_async(
                        query=pending_q,
                        thread_id=f"conv_{st.session_state.active_id}",
                        can_mutate=False,
                        skip_unauthenticated=bool(connected_svcs),
                        allow_auth_gate=False
                    )
                    st.session_state.pending_auth = None
                    st.session_state.last_response = response
                    st.session_state.messages.append(_assistant_message_dict(response))
                    st.rerun()
        with col_act2:
            if st.button("Cancel", key="btn_cancel_auth", use_container_width=True):
                st.session_state.pending_auth = None
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "No problem — I've cancelled that request.",
                    "plan": None, "citations": [], "react_steps": [], "contradictions": [],
                })
                st.rerun()


def render_pending_approval():
    mutation = st.session_state.pending_approval
    if not mutation:
        return
    params = mutation.get("params", {})
    summary_val = params.get("summary", "New action item")
    proj_val = params.get("project_key", "Default project")
    itype_val = params.get("issue_type", "Task")
    desc_val = params.get("description", "No description provided.")

    st.markdown(
        f"""
        <div class="action-card">
            <div class="action-card-header"><span>⚡</span><span>Approve: create a Jira issue</span></div>
            <div class="action-card-desc">I've prepared this task. Review and confirm before I create it in your Jira workspace.</div>
            <div class="action-field-table">
                <p style="margin: 4px 0;"><strong>Title:</strong> {summary_val}</p>
                <p style="margin: 4px 0;"><strong>Project:</strong> <code>{proj_val}</code> &nbsp;|&nbsp; <strong>Type:</strong> {itype_val}</p>
                <p style="margin: 4px 0;"><strong>Description:</strong> {desc_val}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    col_app1, col_app2 = st.columns(2)
    with col_app1:
        if st.button("✅ Approve & create", type="primary", key="btn_approve_mutation", use_container_width=True):
            with st.spinner("Creating Jira issue..."):
                scope = PermissionScope(allowed_scopes=["write:jira"], can_mutate=True)
                res = asyncio.run(st.session_state.agent.tool_registry.jira.connector.mutate(
                    "create_issue", params, scope=scope
                ))
                issue_key = res.get("issue_key", "UNKNOWN")
                succ_msg = f"🎉 Created Jira issue **{issue_key}** (status: {res.get('status', 'Open')})."
                st.success(succ_msg)
                st.session_state.messages.append({
                    "role": "assistant", "content": succ_msg,
                    "plan": None, "citations": [], "react_steps": [], "contradictions": [],
                })
                st.session_state.pending_approval = None
                st.rerun()
    with col_app2:
        if st.button("❌ Reject", key="btn_reject_mutation", use_container_width=True):
            rej_msg = "Okay — I did not create the Jira issue."
            st.info(rej_msg)
            st.session_state.messages.append({
                "role": "assistant", "content": rej_msg,
                "plan": None, "citations": [], "react_steps": [], "contradictions": [],
            })
            st.session_state.pending_approval = None
            st.rerun()


# Empty state.
# The welcome panel is rendered on EVERY run and only hidden once a
# conversation is active. Rendering it conditionally would orphan its elements:
# Streamlit keeps not-yet-overwritten elements from the previous run visible but
# dimmed until the current run finishes, so while a long agent call blocks the
# script the suggestion cards would linger behind the "Thinking…" box.
_show_welcome = (
    len(st.session_state.messages) == 0
    and not st.session_state.pending_auth
    and not st.session_state.pending_approval
    and not st.session_state.preset_query
)
with st.container(key="welcome_panel"):
    render_welcome()
if not _show_welcome:
    st.markdown(
        "<style>.st-key-welcome_panel { display: none; }</style>",
        unsafe_allow_html=True,
    )

# Conversation history
for msg in st.session_state.messages:
    render_message(msg)

# Pending actions
render_auth_challenge()
render_pending_approval()

# ---------------------------------------------------------
# Composer (inline model picker + prompt) + agent execution
# ---------------------------------------------------------
_PROVIDER_MODELS = {
    "zai": ["glm-4.5-air", "glm-4-plus", "glm-4.5", "glm-4-flash", "glm-4-air", "glm-4-long", "glm-4"],
    "opencode": [
        "glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-5.1", "glm-5",
        "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
        "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
        "mimo-v2.5", "mimo-v2.5-pro", "longcat-2.0",
    ],
}
_PROVIDER_LABELS = {"zai": "ZAI GLM", "opencode": "OpenCode Go"}

_vault_inst = _vault()
_active_provider = _vault_inst.get_active_llm_provider()
_llm_creds = _vault_inst.get_credential(_active_provider)
_models = _PROVIDER_MODELS.get(_active_provider, [])
_ai_ready = bool(_llm_creds.get("api_key"))

with st.bottom:
    with st.container(key="composer_box"):
        with st.form("composer", clear_on_submit=True, border=False):
            _c_text, _c_model, _c_send = st.columns([7, 3, 1], vertical_alignment="center")
            with _c_text:
                _composer_text = st.text_input(
                    "Message ContextMesh",
                    placeholder="Message ContextMesh…" if _ai_ready else "Add an AI key in ⚙️ Settings to chat",
                    label_visibility="collapsed",
                    disabled=not _ai_ready,
                    key="composer_text",
                )
            with _c_model:
                _current_model = _llm_creds.get("model") or (_models[0] if _models else "")
                _model_idx = _models.index(_current_model) if _current_model in _models else 0
                _picked_model = st.selectbox(
                    "Model",
                    _models or ["No model"],
                    index=_model_idx,
                    label_visibility="collapsed",
                    disabled=not (_ai_ready and _models),
                    key="composer_model",
                    help=f"Active engine: {_PROVIDER_LABELS.get(_active_provider, _active_provider)}. Model used for planning, retrieval, and answer synthesis.",
                )
            with _c_send:
                _sent = st.form_submit_button("↑", use_container_width=True, disabled=not _ai_ready)

if _sent and _ai_ready and _models and _picked_model != _llm_creds.get("model"):
    _vault_inst.set_credential(_active_provider, {**_llm_creds, "model": _picked_model})

_preset = st.session_state.preset_query
if _preset:
    st.session_state.preset_query = None
user_input = _preset or (_composer_text if (_sent and _ai_ready) else None)

if user_input:
    # Queue the question and repaint immediately. The agent runs on the NEXT
    # script run (below), after the full history — including this question —
    # has been re-rendered uniformly. This keeps the live "Thinking…" turn from
    # interleaving with stale, dimmed leftovers of the previous turn while the
    # (long-running) agent call blocks the script.
    st.session_state.pending_auth = None
    st.session_state.pending_approval = None

    _retitle_active_conversation(user_input)
    st.session_state.messages.append({"role": "user", "content": user_input})
    st.session_state.pending_query = user_input
    st.rerun()

_pending_query = st.session_state.get("pending_query")
if _pending_query:
    with st.chat_message("assistant", avatar="✨"):
        status_box = st.status("Thinking…", expanded=True)
        with status_box:
            console_box = st.container()

        def handle_ui_step(event: dict[str, Any]):
            etype = event.get("type")
            iteration = event.get("iteration", 1)
            if etype == "iteration_start":
                if st.session_state.dev_mode:
                    console_box.markdown(f"**Round {iteration}**")
            elif etype == "thought":
                if st.session_state.dev_mode:
                    console_box.info(f"💭 {event.get('content', '')}")
            elif etype == "action":
                friendly_desc = format_friendly_tool_action(event.get("tool", ""), event.get("arguments", {}) or {})
                console_box.markdown(friendly_desc)
                if st.session_state.dev_mode:
                    console_box.code(
                        f"{event.get('tool')}({json.dumps(event.get('arguments', {}), indent=2, ensure_ascii=False)})",
                        language="python"
                    )
            elif etype == "observation":
                if event.get("success", True):
                    count = event.get("count", 0)
                    console_box.caption(f"↳ {count} result{'s' if count != 1 else ''} found")
                else:
                    console_box.caption(f"↳ ⚠️ {event.get('error', 'Query issue')}")
            elif etype == "synthesis":
                console_box.markdown("🎯 Putting the answer together…")

        try:
            response = run_agent_async(
                query=_pending_query,
                thread_id=f"conv_{st.session_state.active_id}",
                can_mutate=False,
                allow_auth_gate=True,
                on_step=handle_ui_step
            )
        except Exception as exc:
            # Clear the queue so later reruns don't keep retrying a failing run.
            st.session_state.pending_query = None
            logger.exception("Agent run failed for query: %s", _pending_query)
            status_box.update(label="Something went wrong", state="error", expanded=False)
            err_msg = f"⚠️ Sorry — something went wrong while answering that. Please try again. ({exc})"
            st.error(err_msg)
            st.session_state.messages.append({
                "role": "assistant", "content": err_msg,
                "plan": None, "citations": [], "react_steps": [], "contradictions": [],
            })
            st.stop()

        rounds_count = len(response.react_steps) if response.react_steps else 1
        status_box.update(
            label=f"Done · {rounds_count} step{'s' if rounds_count > 1 else ''}",
            state="complete",
            expanded=False
        )

        st.session_state.last_response = response
        st.session_state.pending_query = None

        # Authentication gate pauses the conversation
        if response.auth_required:
            st.session_state.pending_auth = response.auth_challenge
            st.rerun()

        if response.contradictions:
            st.markdown(
                """
                <div class="conflict-alert">
                    <div class="conflict-alert-title">⚠️ Conflicting information across your tools</div>
                </div>
                """,
                unsafe_allow_html=True
            )
            for c in response.contradictions:
                st.markdown(f"- **{c.topic}**: {c.description}")

        display_answer = response.answer if st.session_state.dev_mode else humanize_answer(response.answer)
        st.markdown(display_answer)

        if response.citations:
            render_citation_chips(response.citations)

        st.session_state.messages.append(_assistant_message_dict(response))

        # Human mutation approval pauses the conversation
        if response.requires_approval and response.pending_mutation:
            st.session_state.pending_approval = response.pending_mutation
            st.rerun()

# Keep the newest message in view after every render
scroll_chat_to_bottom()
