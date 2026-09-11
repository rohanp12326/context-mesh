"""Interactive Authentication Wizard and Credential Vault UI for Streamlit.

Streamlined with Composio Model Context Protocol (MCP) for 1-click OAuth app connections.
"""

import asyncio
import os
import streamlit as st

from security.vault import VAULT, mask_secret
from security.connection_testers import (
    test_composio_connection,
    test_composio_app_connection,
    test_zai_connection,
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
        st.markdown(f"⚪ **{label}**: Demo / Unconfigured")


def render_auth_wizard():
    """Render the full onboarding authentication wizard."""
    st.subheader("🔐 Integrations & Authentication Wizard")
    st.markdown(
        "Connect your live enterprise systems via **Composio MCP** (Streamlined 1-Click OAuth) "
        "or switch to demo sandbox mode. All credentials are **encrypted at rest** using AES-128-CBC."
    )

    # Auto-sync with Composio once per session or on demand
    if "composio_auto_synced" not in st.session_state:
        st.session_state["composio_auto_synced"] = True
        try:
            VAULT.sync_composio_connections()
        except Exception:
            pass

    # 1. Global Mode Switcher
    st.markdown("---")
    col_mode1, col_mode2 = st.columns([2, 3])
    current_mode = VAULT.get_connector_mode()

    with col_mode1:
        new_mode = st.radio(
            "⚙️ Active System Mode",
            ["mock", "live"],
            index=0 if current_mode == "mock" else 1,
            format_func=lambda m: "🧪 Demo Sandbox (Synthetic Project Atlas)" if m == "mock" else "🚀 Live Enterprise Mode",
            horizontal=True
        )
        if new_mode != current_mode:
            VAULT.set_connector_mode(new_mode)
            st.success(f"Mode changed to: **{new_mode.upper()}**")
            st.rerun()

    with col_mode2:
        col_lbl, col_sync_btn = st.columns([3, 2])
        with col_lbl:
            st.caption("Live Status Overview:")
        with col_sync_btn:
            if st.button("🔄 Sync Live Auth", key="btn_sync_all_auth", use_container_width=True, help="Queries Composio for newly connected OAuth apps"):
                with st.spinner("Checking active connections..."):
                    VAULT.sync_composio_connections()
                    st.rerun()

        status = VAULT.get_status()["services"]
        c0, c1, c2, c3, c4 = st.columns(5)
        with c0:
            render_status_pill(status.get("composio", {}).get("is_configured", False), "Composio")
        with c1:
            render_status_pill(status["zai"]["is_configured"], "ZAI GLM")
        with c2:
            render_status_pill(status["jira"]["is_configured"], "Jira")
        with c3:
            render_status_pill(status["slack"]["is_configured"], "Slack")
        with c4:
            render_status_pill(status["gmail"]["is_configured"], "Gmail")

    st.markdown("---")

    # 2. Service Cards
    card_composio, card_zai, card_jira, card_slack, card_gmail = st.tabs([
        "⚡ Composio MCP Gateway",
        "🤖 ZAI GLM API",
        "📌 Atlassian Jira",
        "💬 Slack",
        "📧 Gmail"
    ])

    # ------------------ COMPOSIO MCP GATEWAY ------------------
    with card_composio:
        st.markdown("### ⚡ Composio Model Context Protocol (MCP) Gateway")
        st.info(
            "**Streamlined App Authentication & Unified Tool Execution:**\n"
            "Composio replaces disparate tokens, app passwords, and developer apps with a single master key. "
            "Once configured, you can connect **Jira**, **Slack**, and **Gmail** using 1-click official OAuth in your browser!"
        )

        col_cb1, col_cb2 = st.columns(2)
        with col_cb1:
            st.link_button("🔗 Open Composio Dashboard", "https://dashboard.composio.dev/", use_container_width=True)
        with col_cb2:
            st.link_button("🔗 Composio MCP Documentation", "https://docs.composio.dev/", use_container_width=True)

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
                placeholder="e.g. comp_live_...",
                help="Your key is encrypted and stored locally in .secrets/vault.enc"
            )
        with col_ck2:
            comp_user_input = st.text_input(
                "Scoped User ID",
                value=default_comp_user,
                help="Identifier for grouping connected accounts (e.g. default_user or your email)"
            )

        comp_url_input = st.text_input(
            "Composio API Base URL",
            value=default_comp_url,
            help="Default: https://backend.composio.dev/api/v3.1"
        )

        col_cbtn1, col_cbtn2 = st.columns([1, 4])
        with col_cbtn1:
            if st.button("⚡ Test & Save Composio Key", key="btn_save_composio"):
                if not comp_key_input:
                    st.error("Please enter your Composio API Key.")
                else:
                    with st.spinner("Connecting to Composio API..."):
                        ok, msg, latency = asyncio.run(test_composio_connection(
                            api_key=comp_key_input,
                            base_url=comp_url_input
                        ))
                        if ok:
                            VAULT.set_credential("composio", {
                                "api_key": comp_key_input.strip(),
                                "user_id": comp_user_input.strip(),
                                "base_url": comp_url_input.strip()
                            })
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msg}")

        with col_cbtn2:
            if comp_creds.get("api_key"):
                st.caption(f"Stored Key: `{mask_secret(comp_creds.get('api_key'))}` | User: `{comp_creds.get('user_id', 'default_user')}`")

        # Display currently connected accounts from Composio
        if comp_creds.get("api_key"):
            st.markdown("#### 📱 Active Composio Connected Accounts")
            if st.button("🔄 Refresh & Sync Accounts", key="btn_refresh_composio_accs"):
                with st.spinner("Querying active connections from Composio..."):
                    try:
                        accs = asyncio.run(COMPOSIO_CLIENT.list_connected_accounts())
                        VAULT.sync_composio_connections(accs)
                        if accs:
                            for a in accs:
                                tk = a.get("toolkit")
                                slug = tk.get("slug") if isinstance(tk, dict) else tk
                                status_icon = "🟢" if a.get("status") == "ACTIVE" else "⚪"
                                st.write(f"- {status_icon} **{str(slug).upper()}**: Status `{a.get('status')}` (ID: `{a.get('id')}`)")
                        else:
                            st.info("No connected accounts found yet. Connect Jira, Slack, or Gmail in their respective tabs below!")
                        st.rerun()
                    except Exception as e:
                        st.warning(f"Could not list accounts: {e}")

    # ------------------ ZAI GLM API ------------------
    with card_zai:
        st.markdown("### 🤖 ZAI GLM API Configuration")
        st.info("ContextMesh uses ZAI's GLM foundation models for query decomposition, risk analysis, and cited answer synthesis.")

        col_zb1, col_zb2 = st.columns(2)
        with col_zb1:
            st.link_button("🔗 Open Zhipu BigModel Console", "https://open.bigmodel.cn/usercenter/apikeys", use_container_width=True)
        with col_zb2:
            st.link_button("🔗 Open Z.ai Global Platform", "https://z.ai/", use_container_width=True)

        zai_creds = VAULT.get_credential("zai")
        default_key = zai_creds.get("api_key", os.getenv("ZAI_API_KEY", ""))
        default_model = zai_creds.get("model", os.getenv("ZAI_MODEL", "glm-4.5-air"))
        default_url = zai_creds.get("base_url", os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"))

        col_z1, col_z2 = st.columns([3, 2])
        with col_z1:
            zai_key_input = st.text_input("ZAI API Key", value=default_key, type="password")

        model_presets = ["glm-4.5-air", "glm-4-plus", "glm-4.5", "glm-4-flash", "glm-4-air", "glm-4-long", "glm-4"]
        initial_idx = model_presets.index(default_model) if default_model in model_presets else 0

        with col_z2:
            zai_model_select = st.selectbox("GLM Model", model_presets, index=initial_idx)

        zai_url_input = st.text_input("API Base URL", value=default_url)

        if st.button("⚡ Test & Save ZAI Key", key="btn_save_zai"):
            if not zai_key_input:
                st.error("Please enter an API key.")
            else:
                with st.spinner("Connecting to ZAI GLM..."):
                    ok, msg, _ = asyncio.run(test_zai_connection(zai_key_input, zai_url_input, zai_model_select))
                    if ok:
                        VAULT.set_credential("zai", {"api_key": zai_key_input.strip(), "model": zai_model_select, "base_url": zai_url_input.strip()})
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

    # ------------------ ATLASSIAN JIRA ------------------
    with card_jira:
        st.markdown("### 📌 Atlassian Jira Configuration")
        st.info("Retrieve live sprints, blockers, epics, and execute approval-gated issue creations.")

        jira_creds = VAULT.get_credential("jira")
        jira_auth_type = st.radio(
            "Authentication Method",
            ["⚡ Streamlined 1-Click OAuth via Composio (Recommended)", "Atlassian Cloud REST API (Manual API Token)"],
            horizontal=True,
            key="wizard_jira_auth_type"
        )

        if "Composio" in jira_auth_type:
            st.markdown("#### ⚡ Streamlined 1-Click Connect via Composio")
            st.caption("No manual API tokens or Atlassian admin permissions needed. Simply click to authorize in your browser.")

            col_ja1, col_ja2 = st.columns(2)
            with col_ja1:
                if st.button("🔗 Generate Jira Connect Link", key="btn_gen_jira_link"):
                    with st.spinner("Generating secure OAuth connect URL from Composio..."):
                        try:
                            auth_url = asyncio.run(COMPOSIO_CLIENT.get_auth_url("jira"))
                            st.session_state["jira_connect_url"] = auth_url
                            st.success("Connect link generated successfully!")
                        except Exception as e:
                            st.error(f"Failed to generate link: {e}")

            if st.session_state.get("jira_connect_url"):
                st.link_button("👉 Open Official Atlassian OAuth Page", st.session_state["jira_connect_url"], type="primary", use_container_width=True)
                st.caption(f"Link: `{st.session_state['jira_connect_url']}`")

            with col_ja2:
                if st.button("🔄 Verify Jira Connection Status", key="btn_check_jira_status"):
                    with st.spinner("Checking active connection with Composio..."):
                        ok, msg, _ = asyncio.run(test_composio_app_connection("jira", user_id=comp_user_input))
                        if ok:
                            data = dict(jira_creds)
                            data.update({"auth_type": "composio", "composio_connected": True})
                            VAULT.set_credential("jira", data)
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.warning(f"⚠️ {msg}")

            if jira_creds.get("composio_connected") or VAULT.is_service_authenticated("jira"):
                st.markdown("🟢 **Status**: Authenticated and connected via Composio MCP!")
            else:
                st.markdown("⚪ **Status**: Not connected. Click 'Generate Jira Connect Link' above.")

        else:
            st.markdown("#### 🔑 Manual REST API Credentials")
            st.link_button("🔗 Open Atlassian API Token Page", "https://id.atlassian.com/manage-profile/security/api-tokens", use_container_width=True)
            jira_url = st.text_input("Jira Instance URL", value=jira_creds.get("base_url", ""), placeholder="https://your-company.atlassian.net")
            jira_email = st.text_input("Atlassian User Email", value=jira_creds.get("user_email", ""), placeholder="you@company.com")
            jira_token = st.text_input("Atlassian API Token", value=jira_creds.get("api_token", ""), type="password", placeholder="ATATT3xFfGF0...")

            if st.button("⚡ Test & Save Manual Jira Connection", key="btn_save_jira_manual"):
                with st.spinner("Verifying Jira credentials via Atlassian REST API..."):
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
        st.markdown("### 💬 Slack Configuration")
        st.info("Query project channels, release updates, canvases, and discussion threads across your Slack workspace.")

        slack_creds = VAULT.get_credential("slack")
        slack_auth_type = st.radio(
            "Authentication Method",
            ["⚡ Streamlined 1-Click OAuth via Composio (Recommended)", "Slack Bot/User Token (Manual Web API)"],
            horizontal=True,
            key="wizard_slack_auth_type"
        )

        if "Composio" in slack_auth_type:
            st.markdown("#### ⚡ Streamlined 1-Click Connect via Composio")
            st.caption("No need to create a Slack app or manage bot scopes manually. Click below to authorize your workspace.")

            col_sa1, col_sa2 = st.columns(2)
            with col_sa1:
                if st.button("🔗 Generate Slack Connect Link", key="btn_gen_slack_link"):
                    with st.spinner("Generating secure OAuth connect URL from Composio..."):
                        try:
                            auth_url = asyncio.run(COMPOSIO_CLIENT.get_auth_url("slack"))
                            st.session_state["slack_connect_url"] = auth_url
                            st.success("Connect link generated successfully!")
                        except Exception as e:
                            st.error(f"Failed to generate link: {e}")

            if st.session_state.get("slack_connect_url"):
                st.link_button("👉 Open Official Slack OAuth Page", st.session_state["slack_connect_url"], type="primary", use_container_width=True)
                st.caption(f"Link: `{st.session_state['slack_connect_url']}`")

            with col_sa2:
                if st.button("🔄 Verify Slack Connection Status", key="btn_check_slack_status"):
                    with st.spinner("Checking active connection with Composio..."):
                        ok, msg, _ = asyncio.run(test_composio_app_connection("slack", user_id=comp_user_input))
                        if ok:
                            data = dict(slack_creds)
                            data.update({"auth_type": "composio", "composio_connected": True})
                            VAULT.set_credential("slack", data)
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.warning(f"⚠️ {msg}")

            if slack_creds.get("composio_connected") or VAULT.is_service_authenticated("slack"):
                st.markdown("🟢 **Status**: Authenticated and connected via Composio MCP!")
            else:
                st.markdown("⚪ **Status**: Not connected. Click 'Generate Slack Connect Link' above.")

        else:
            st.markdown("#### 🔑 Manual Slack App Token")
            st.link_button("🔗 Open Slack API Apps Console", "https://api.slack.com/apps", use_container_width=True)
            default_slack_token = slack_creds.get("bot_token") or slack_creds.get("user_token") or ""
            slack_token = st.text_input("Slack Bot/User Token", value=default_slack_token, type="password", placeholder="xoxb-... or xoxp-...")

            if st.button("⚡ Test & Save Manual Slack Connection", key="btn_save_slack_manual"):
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
        st.markdown("### 📧 Gmail Configuration")
        st.info("Search email threads for stakeholder commitments, release announcements, and approvals.")

        gmail_creds = VAULT.get_credential("gmail")
        gmail_auth_type = st.radio(
            "Authentication Method",
            [
                "⚡ Streamlined 1-Click OAuth via Composio (Recommended)",
                "Google App Password (16 chars)",
                "OAuth2 Bearer Token"
            ],
            horizontal=True,
            key="wizard_gmail_auth_type"
        )

        if "Composio" in gmail_auth_type:
            st.markdown("#### ⚡ Streamlined 1-Click Connect via Composio")
            st.caption("No 16-character App Passwords or Google Cloud Console setup required. Authorize directly with Google OAuth.")

            col_ga1, col_ga2 = st.columns(2)
            with col_ga1:
                if st.button("🔗 Generate Gmail Connect Link", key="btn_gen_gmail_link"):
                    with st.spinner("Generating secure OAuth connect URL from Composio..."):
                        try:
                            auth_url = asyncio.run(COMPOSIO_CLIENT.get_auth_url("gmail"))
                            st.session_state["gmail_connect_url"] = auth_url
                            st.success("Connect link generated successfully!")
                        except Exception as e:
                            st.error(f"Failed to generate link: {e}")

            if st.session_state.get("gmail_connect_url"):
                st.link_button("👉 Open Official Google OAuth Page", st.session_state["gmail_connect_url"], type="primary", use_container_width=True)
                st.caption(f"Link: `{st.session_state['gmail_connect_url']}`")

            with col_ga2:
                if st.button("🔄 Verify Gmail Connection Status", key="btn_check_gmail_status"):
                    with st.spinner("Checking active connection with Composio..."):
                        ok, msg, _ = asyncio.run(test_composio_app_connection("gmail", user_id=comp_user_input))
                        if ok:
                            data = dict(gmail_creds)
                            data.update({"auth_type": "composio", "composio_connected": True})
                            VAULT.set_credential("gmail", data)
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.warning(f"⚠️ {msg}")

            if gmail_creds.get("composio_connected") or VAULT.is_service_authenticated("gmail"):
                st.markdown("🟢 **Status**: Authenticated and connected via Composio MCP!")
            else:
                st.markdown("⚪ **Status**: Not connected. Click 'Generate Gmail Connect Link' above.")

        else:
            st.markdown("#### 🔑 Manual Google App Password or Token")
            gmail_account = st.text_input("Gmail Address", value=gmail_creds.get("account", ""), placeholder="you@company.com", key="wizard_gmail_email")

            if "App Password" in gmail_auth_type:
                st.link_button("🔗 Open Google App Passwords Page", "https://myaccount.google.com/apppasswords", use_container_width=True)
                gmail_app_pw = st.text_input("16-character App Password", value=gmail_creds.get("app_password", ""), type="password", placeholder="xxxx xxxx xxxx xxxx", key="wizard_gmail_pw")
                gmail_token = None
            else:
                st.link_button("🔗 Open Google OAuth2 Playground", "https://developers.google.com/oauthplayground", use_container_width=True)
                gmail_token = st.text_input("OAuth2 Bearer Access Token", value=gmail_creds.get("access_token", ""), type="password", placeholder="ya29.a0...", key="wizard_gmail_token")
                gmail_app_pw = None

            if st.button("⚡ Test & Save Manual Gmail Connection", key="btn_save_gmail_manual"):
                with st.spinner("Verifying live connection to Gmail..."):
                    ok, msg, _ = asyncio.run(test_gmail_connection(
                        account_email=gmail_account,
                        app_password=gmail_app_pw,
                        access_token=gmail_token
                    ))
                    if ok:
                        data = dict(gmail_creds)
                        data["account"] = gmail_account
                        if gmail_app_pw:
                            data["app_password"] = gmail_app_pw
                        if gmail_token:
                            data["access_token"] = gmail_token
                        data.update({"auth_type": "manual", "composio_connected": False})
                        VAULT.set_credential("gmail", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
