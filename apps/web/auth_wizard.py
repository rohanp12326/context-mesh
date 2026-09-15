"""Interactive Authentication Wizard and Credential Vault UI for Streamlit.

Streamlined with Composio Model Context Protocol (MCP) for 1-click OAuth app connections.
Designed for non-technical users with clear guidance, 1-click browser OAuth, and safe credential storage.
"""

import asyncio
import os
import streamlit as st

from security.vault import VAULT, mask_secret
from security.connection_testers import (
    test_composio_connection,
    test_composio_app_connection,
    test_zai_connection,
    test_opencode_connection,
    test_jira_connection,
    test_slack_connection,
    test_gmail_connection,
)
from mcp_servers.composio_client import COMPOSIO_CLIENT


def render_status_pill(is_configured: bool, label: str):
    """Render a colored status badge for a service."""
    if is_configured:
        st.markdown(f"🟢 **{label}**: Connected")
    else:
        st.markdown(f"⚪ **{label}**: Not Connected")


def render_auth_wizard():
    """Render the full onboarding authentication wizard."""
    st.markdown("### 🔌 Connected Apps & Integrations")
    st.caption(
        "Connect your team's tools so ContextMesh can search issues, discussions, and emails. "
        "All connections use secure, official 1-click OAuth and credentials are encrypted at rest."
    )

    # Auto-sync with Composio once per session or on demand
    if "composio_auto_synced" not in st.session_state:
        st.session_state["composio_auto_synced"] = True
        try:
            VAULT.sync_composio_connections()
        except Exception:
            pass

    # Status Overview Bar
    with st.container(border=True):
        col_lbl, col_sync_btn = st.columns([4, 1])
        with col_lbl:
            st.markdown("##### 📡 Live App Status Overview")
        with col_sync_btn:
            if st.button("🔄 Sync Status", key="btn_sync_all_auth", use_container_width=True, help="Queries Composio for active OAuth connections"):
                with st.spinner("Checking active connections..."):
                    VAULT.sync_composio_connections()
                    st.rerun()

        status = VAULT.get_status()["services"]
        c0, c1, c2, c3, c4 = st.columns(5)
        with c0:
            render_status_pill(status.get("composio", {}).get("is_configured", False), "Composio")
        with c1:
            render_status_pill(status["zai"]["is_configured"], "AI Engine")
        with c2:
            render_status_pill(status["jira"]["is_configured"], "Jira")
        with c3:
            render_status_pill(status["slack"]["is_configured"], "Slack")
        with c4:
            render_status_pill(status["gmail"]["is_configured"], "Gmail")

    st.markdown("---")

    # Service Tabs (5 tabs for compatibility)
    card_composio, card_zai, card_jira, card_slack, card_gmail = st.tabs([
        "⚡ Composio Gateway",
        "🤖 AI Engine (ZAI / OpenCode)",
        "📌 Atlassian Jira",
        "💬 Slack",
        "📧 Gmail"
    ])

    # ------------------ ATLASSIAN JIRA ------------------
    with card_jira:
        st.markdown("#### 📌 Atlassian Jira")
        st.caption("Connect Jira to search tickets, epics, sprint blockers, and create tasks with human approval.")

        jira_creds = VAULT.get_credential("jira")
        is_jira_conn = jira_creds.get("composio_connected") or VAULT.is_service_authenticated("jira")
        
        if is_jira_conn:
            st.success("✅ **Jira is Connected & Active**: The assistant can search your Jira issues.")
        else:
            st.info("⚪ **Jira is Not Connected**: Click below to authorize Jira with 1-click official OAuth.")

        st.markdown("##### ⚡ Connect Jira (1-Click OAuth)")
        col_ja1, col_ja2 = st.columns(2)
        with col_ja1:
            if st.button("🔗 Generate Jira Connect Link", key="btn_gen_jira_link", use_container_width=True):
                with st.spinner("Generating secure OAuth connect URL..."):
                    try:
                        auth_url = asyncio.run(COMPOSIO_CLIENT.get_auth_url("jira"))
                        st.session_state["jira_connect_url"] = auth_url
                        st.success("Authorization link ready!")
                    except Exception as e:
                        st.error(f"Failed to generate link: {e}")

        if st.session_state.get("jira_connect_url"):
            st.link_button("👉 Authorize Jira in Browser", st.session_state["jira_connect_url"], type="primary", use_container_width=True)

        with col_ja2:
            if st.button("🔄 Verify Jira Connection", key="btn_check_jira_status", use_container_width=True):
                with st.spinner("Checking active connection..."):
                    comp_creds = VAULT.get_credential("composio")
                    u_id = comp_creds.get("user_id", "default_user")
                    ok, msg, _ = asyncio.run(test_composio_app_connection("jira", user_id=u_id))
                    if ok:
                        data = dict(jira_creds)
                        data.update({"auth_type": "composio", "composio_connected": True})
                        VAULT.set_credential("jira", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.warning(f"⚠️ {msg}")

        # Advanced Manual Options collapsed
        with st.expander("⚙️ Advanced: Connect via Manual Atlassian API Token", expanded=False):
            st.caption("Use manual credentials if your organization does not support Composio OAuth.")
            jira_url = st.text_input("Jira Instance URL", value=jira_creds.get("base_url", ""), placeholder="https://your-company.atlassian.net", key="input_jira_url")
            jira_email = st.text_input("Atlassian User Email", value=jira_creds.get("user_email", ""), placeholder="you@company.com", key="input_jira_email")
            jira_token = st.text_input("Atlassian API Token", value=jira_creds.get("api_token", ""), type="password", placeholder="ATATT3xFfGF0...", key="input_jira_token")

            if st.button("⚡ Test & Save Manual Jira Token", key="btn_save_jira_manual"):
                with st.spinner("Verifying credentials with Jira REST API..."):
                    ok, msg, _ = asyncio.run(test_jira_connection(jira_url, jira_email, jira_token))
                    if ok:
                        data = dict(jira_creds)
                        data.update({"base_url": jira_url, "user_email": jira_email, "api_token": jira_token, "auth_type": "token", "composio_connected": False})
                        VAULT.set_credential("jira", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

    # ------------------ SLACK ------------------
    with card_slack:
        st.markdown("#### 💬 Slack")
        st.caption("Connect Slack to search team channels, project updates, canvases, and discussion threads.")

        slack_creds = VAULT.get_credential("slack")
        is_slack_conn = slack_creds.get("composio_connected") or VAULT.is_service_authenticated("slack")

        if is_slack_conn:
            st.success("✅ **Slack is Connected & Active**: The assistant can search your team's Slack channels.")
        else:
            st.info("⚪ **Slack is Not Connected**: Click below to authorize your Slack workspace.")

        st.markdown("##### ⚡ Connect Slack (1-Click OAuth)")
        col_sa1, col_sa2 = st.columns(2)
        with col_sa1:
            if st.button("🔗 Generate Slack Connect Link", key="btn_gen_slack_link", use_container_width=True):
                with st.spinner("Generating secure OAuth connect URL..."):
                    try:
                        auth_url = asyncio.run(COMPOSIO_CLIENT.get_auth_url("slack"))
                        st.session_state["slack_connect_url"] = auth_url
                        st.success("Authorization link ready!")
                    except Exception as e:
                        st.error(f"Failed to generate link: {e}")

        if st.session_state.get("slack_connect_url"):
            st.link_button("👉 Authorize Slack in Browser", st.session_state["slack_connect_url"], type="primary", use_container_width=True)

        with col_sa2:
            if st.button("🔄 Verify Slack Connection", key="btn_check_slack_status", use_container_width=True):
                with st.spinner("Checking active connection..."):
                    comp_creds = VAULT.get_credential("composio")
                    u_id = comp_creds.get("user_id", "default_user")
                    ok, msg, _ = asyncio.run(test_composio_app_connection("slack", user_id=u_id))
                    if ok:
                        data = dict(slack_creds)
                        data.update({"auth_type": "composio", "composio_connected": True})
                        VAULT.set_credential("slack", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.warning(f"⚠️ {msg}")

        # Advanced Manual Options collapsed
        with st.expander("⚙️ Advanced: Connect via Manual Slack Bot Token", expanded=False):
            st.caption("Enter a `xoxb-` or `xoxp-` token if manually self-hosting a Slack app.")
            default_slack_token = slack_creds.get("bot_token") or slack_creds.get("user_token") or ""
            slack_token = st.text_input("Slack Bot/User Token", value=default_slack_token, type="password", placeholder="xoxb-... or xoxp-...", key="input_slack_token")

            if st.button("⚡ Test & Save Manual Slack Token", key="btn_save_slack_manual"):
                with st.spinner("Connecting to Slack API..."):
                    ok, msg, _ = asyncio.run(test_slack_connection(slack_token))
                    if ok:
                        data = dict(slack_creds)
                        token_field = "user_token" if slack_token.startswith("xoxp-") else "bot_token"
                        data[token_field] = slack_token.strip()
                        data.update({"auth_type": "token", "composio_connected": False})
                        VAULT.set_credential("slack", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

    # ------------------ GMAIL ------------------
    with card_gmail:
        st.markdown("#### 📧 Gmail")
        st.caption("Connect Gmail to search email threads for stakeholder commitments, release announcements, and approvals.")

        gmail_creds = VAULT.get_credential("gmail")
        is_gmail_conn = gmail_creds.get("composio_connected") or VAULT.is_service_authenticated("gmail")

        if is_gmail_conn:
            st.success("✅ **Gmail is Connected & Active**: The assistant can search email threads.")
        else:
            st.info("⚪ **Gmail is Not Connected**: Click below to authorize Gmail with 1-click official Google OAuth.")

        st.markdown("##### ⚡ Connect Gmail (1-Click OAuth)")
        col_ga1, col_ga2 = st.columns(2)
        with col_ga1:
            if st.button("🔗 Generate Gmail Connect Link", key="btn_gen_gmail_link", use_container_width=True):
                with st.spinner("Generating secure OAuth connect URL..."):
                    try:
                        auth_url = asyncio.run(COMPOSIO_CLIENT.get_auth_url("gmail"))
                        st.session_state["gmail_connect_url"] = auth_url
                        st.success("Authorization link ready!")
                    except Exception as e:
                        st.error(f"Failed to generate link: {e}")

        if st.session_state.get("gmail_connect_url"):
            st.link_button("👉 Authorize Gmail in Browser", st.session_state["gmail_connect_url"], type="primary", use_container_width=True)

        with col_ga2:
            if st.button("🔄 Verify Gmail Connection", key="btn_check_gmail_status", use_container_width=True):
                with st.spinner("Checking active connection..."):
                    comp_creds = VAULT.get_credential("composio")
                    u_id = comp_creds.get("user_id", "default_user")
                    ok, msg, _ = asyncio.run(test_composio_app_connection("gmail", user_id=u_id))
                    if ok:
                        data = dict(gmail_creds)
                        data.update({"auth_type": "composio", "composio_connected": True})
                        VAULT.set_credential("gmail", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.warning(f"⚠️ {msg}")

        # Advanced Manual Options collapsed
        with st.expander("⚙️ Advanced: Connect via Google App Password", expanded=False):
            st.caption("Use a 16-character Google App Password if not using OAuth.")
            gmail_account = st.text_input("Gmail Address", value=gmail_creds.get("account", ""), placeholder="you@company.com", key="wizard_gmail_email")
            gmail_app_pw = st.text_input("16-character App Password", value=gmail_creds.get("app_password", ""), type="password", placeholder="xxxx xxxx xxxx xxxx", key="wizard_gmail_pw")

            if st.button("⚡ Test & Save Manual Gmail Password", key="btn_save_gmail_manual"):
                with st.spinner("Verifying connection to Gmail IMAP..."):
                    ok, msg, _ = asyncio.run(test_gmail_connection(
                        account_email=gmail_account,
                        app_password=gmail_app_pw
                    ))
                    if ok:
                        data = dict(gmail_creds)
                        data["account"] = gmail_account
                        data["app_password"] = gmail_app_pw
                        data.update({"auth_type": "manual", "composio_connected": False})
                        VAULT.set_credential("gmail", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

    # ------------------ COMPOSIO MCP GATEWAY ------------------
    with card_composio:
        st.markdown("#### ⚡ Composio MCP Gateway")
        st.caption(
            "Composio powers unified 1-click official OAuth for Jira, Slack, and Gmail. "
            "Configure your master key below to unlock browser-based authorization."
        )

        comp_creds = VAULT.get_credential("composio")
        default_comp_key = comp_creds.get("api_key", os.getenv("COMPOSIO_API_KEY", ""))
        default_comp_user = comp_creds.get("user_id", os.getenv("COMPOSIO_USER_ID", "default_user"))
        default_comp_url = comp_creds.get("base_url", os.getenv("COMPOSIO_BASE_URL", "https://backend.composio.dev/api/v3.1"))

        col_ck1, col_ck2 = st.columns([3, 2])
        with col_ck1:
            comp_key_input = st.text_input(
                "Composio Master API Key",
                value=default_comp_key,
                type="password",
                placeholder="comp_live_...",
                help="Encrypted locally using AES-128",
                key="input_comp_key"
            )
        with col_ck2:
            comp_user_input = st.text_input(
                "User Account Identifier",
                value=default_comp_user,
                help="User id for grouping connected apps",
                key="input_comp_user"
            )

        col_cbtn1, col_cbtn2 = st.columns([1, 2])
        with col_cbtn1:
            if st.button("⚡ Test & Save Composio Key", key="btn_save_composio", use_container_width=True):
                if not comp_key_input:
                    st.error("Please enter your Composio API Key.")
                else:
                    with st.spinner("Connecting to Composio API..."):
                        ok, msg, _ = asyncio.run(test_composio_connection(
                            api_key=comp_key_input,
                            base_url=default_comp_url
                        ))
                        if ok:
                            VAULT.set_credential("composio", {
                                "api_key": comp_key_input.strip(),
                                "user_id": comp_user_input.strip(),
                                "base_url": default_comp_url.strip()
                            })
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msg}")

        with col_cbtn2:
            if comp_creds.get("api_key"):
                st.caption(f"Active Key: `{mask_secret(comp_creds.get('api_key'))}` | User: `{comp_creds.get('user_id', 'default_user')}`")

    # ------------------ AI ENGINE (ZAI GLM / OpenCode Go) ------------------
    with card_zai:
        st.markdown("#### 🤖 AI Foundation Model")
        st.caption("Select the LLM engine for reasoning, query decomposition, and synthesized answers.")

        active_provider = VAULT.get_active_llm_provider()
        provider_presets = {"ZAI GLM (Zhipu BigModel)": "zai", "OpenCode Go (Subscription)": "opencode"}
        provider_label_to_id = {label: pid for label, pid in provider_presets.items()}

        default_label = next((l for l, pid in provider_presets.items() if pid == active_provider), "ZAI GLM (Zhipu BigModel)")
        selected_label = st.radio(
            "Active AI Engine",
            list(provider_presets.keys()),
            index=list(provider_presets.keys()).index(default_label),
            key="select_llm_provider",
            horizontal=True,
        )
        selected_provider = provider_label_to_id[selected_label] if selected_label else "zai"

        if selected_provider == "zai":
            zai_creds = VAULT.get_credential("zai")
            default_key = zai_creds.get("api_key", os.getenv("ZAI_API_KEY", ""))
            default_model = zai_creds.get("model", os.getenv("ZAI_MODEL", "glm-4.5-air"))
            default_url = zai_creds.get("base_url", os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"))

            col_z1, col_z2 = st.columns([3, 2])
            with col_z1:
                zai_key_input = st.text_input("ZAI API Key", value=default_key, type="password", key="input_zai_key")

            model_presets = ["glm-4.5-air", "glm-4-plus", "glm-4.5", "glm-4-flash", "glm-4-air", "glm-4-long", "glm-4"]
            initial_idx = model_presets.index(default_model) if default_model in model_presets else 0

            with col_z2:
                zai_model_select = st.selectbox("Model Engine", model_presets, index=initial_idx, key="select_zai_model")

            if st.button("⚡ Test & Save AI Key", key="btn_save_zai"):
                if not zai_key_input:
                    st.error("Please enter your ZAI API Key.")
                else:
                    with st.spinner("Connecting to ZAI GLM..."):
                        ok, msg, _ = asyncio.run(test_zai_connection(zai_key_input, default_url, zai_model_select))
                        if ok:
                            VAULT.set_credential("zai", {"api_key": zai_key_input.strip(), "model": zai_model_select, "base_url": default_url.strip()})
                            VAULT.set_active_llm_provider("zai")
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msg}")

            if zai_creds.get("api_key"):
                st.caption(f"Active Key: `{mask_secret(zai_creds.get('api_key'))}` | Model: `{zai_creds.get('model', '')}`")

        else:
            oc_creds = VAULT.get_credential("opencode")
            oc_default_key = oc_creds.get("api_key", os.getenv("OPENCODE_API_KEY", ""))
            oc_default_model = oc_creds.get("model", os.getenv("OPENCODE_MODEL", "glm-5.3-flash"))
            oc_default_url = oc_creds.get("base_url", os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1"))

            col_o1, col_o2 = st.columns([3, 2])
            with col_o1:
                oc_key_input = st.text_input(
                    "OpenCode API Key",
                    value=oc_default_key,
                    type="password",
                    key="input_opencode_key",
                    help="Copy your API key from the OpenCode console at opencode.ai/auth"
                )

            oc_model_presets = [
                "glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-5.1", "glm-5",
                "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
                "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
                "mimo-v2.5", "mimo-v2.5-pro", "longcat-2.0",
            ]
            oc_initial_idx = oc_model_presets.index(oc_default_model) if oc_default_model in oc_model_presets else 0

            with col_o2:
                oc_model_select = st.selectbox("Model Engine", oc_model_presets, index=oc_initial_idx, key="select_opencode_model")

            if st.button("⚡ Test & Save OpenCode Key", key="btn_save_opencode"):
                if not oc_key_input:
                    st.error("Please enter your OpenCode API Key.")
                else:
                    with st.spinner("Connecting to OpenCode..."):
                        ok, msg, _ = asyncio.run(test_opencode_connection(oc_key_input, oc_default_url, oc_model_select))
                        if ok:
                            VAULT.set_credential("opencode", {"api_key": oc_key_input.strip(), "model": oc_model_select, "base_url": oc_default_url.strip()})
                            VAULT.set_active_llm_provider("opencode")
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msg}")

            if oc_creds.get("api_key"):
                st.caption(f"Active Key: `{mask_secret(oc_creds.get('api_key'))}` | Model: `{oc_creds.get('model', '')}`")
