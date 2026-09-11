"""Interactive Authentication Wizard and Credential Vault UI for Streamlit."""

import asyncio
import os
import streamlit as st

from security.vault import VAULT, mask_secret
from security.connection_testers import (
    test_zai_connection,
    test_jira_connection,
    test_slack_connection,
    test_gmail_connection,
    test_mcp_connection,
)


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
        "Connect your live enterprise systems or switch to demo sandbox mode. "
        "All credentials are **encrypted at rest** using AES-128-CBC (Fernet) in a secure local vault."
    )

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
        st.caption("Live Status Overview:")
        status = VAULT.get_status()["services"]
        c1, c2, c3, c4 = st.columns(4)
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
    card_zai, card_jira, card_slack, card_gmail = st.tabs([
        "🤖 ZAI GLM API",
        "📌 Atlassian Jira",
        "💬 Slack",
        "📧 Gmail"
    ])

    # ------------------ ZAI GLM API ------------------
    with card_zai:
        st.markdown("### 🤖 ZAI GLM API Configuration")
        st.info(
            "ContextMesh uses ZAI's GLM foundation models for query decomposition, risk analysis, and cited answer synthesis."
        )

        col_zb1, col_zb2 = st.columns(2)
        with col_zb1:
            st.link_button("🔗 Open Zhipu BigModel Console", "https://open.bigmodel.cn/usercenter/apikeys", use_container_width=True)
        with col_zb2:
            st.link_button("🔗 Open Z.ai Global Platform", "https://z.ai/", use_container_width=True)

        with st.expander("📖 Step-by-Step Guide: How to obtain your ZAI API Key & Endpoints", expanded=False):
            st.markdown("""
            1. **Account Platforms**:
               - **Mainland China (BigModel)**: [open.bigmodel.cn](https://open.bigmodel.cn/) (Base URL: `https://open.bigmodel.cn/api/paas/v4/`)
               - **Global Platform (Z.ai)**: [z.ai](https://z.ai/) (Base URL: `https://api.z.ai/api/paas/v4/`)
               - **Coding Plan**: (Base URL: `https://api.z.ai/api/coding/paas/v4/`)
            2. Obtain API Key from the platform console (**API Keys** / **User Center**).
            3. **Recommended Model**: `glm-4.5-air` (high-speed hybrid MoE agent reasoning).
            > **Note on Error 1211 (`模型不存在`)**: Some lightweight models like `glm-4-flash` may not be available on all account tiers. If you encounter error 1211, switch to `glm-4.5-air` or `glm-4-plus`.
            """)

        zai_creds = VAULT.get_credential("zai")
        default_key = zai_creds.get("api_key", os.getenv("ZAI_API_KEY", ""))
        default_model = zai_creds.get("model", os.getenv("ZAI_MODEL", "glm-4.5-air"))
        default_url = zai_creds.get("base_url", os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"))

        col_z1, col_z2 = st.columns([3, 2])
        with col_z1:
            zai_key_input = st.text_input(
                "ZAI API Key",
                value=default_key,
                type="password",
                placeholder="e.g. 74xxxxxxxxxx.xxxxxxxxxxxx",
                help="Your key is encrypted and stored locally in .secrets/vault.enc"
            )

        model_presets = [
            "glm-4.5-air",
            "glm-4-plus",
            "glm-4.5",
            "glm-4-flash",
            "glm-4-air",
            "glm-4-long",
            "glm-4",
            "Custom (Specify below)..."
        ]

        if default_model in model_presets[:-1]:
            initial_idx = model_presets.index(default_model)
        elif default_model:
            initial_idx = model_presets.index("Custom (Specify below)...")
        else:
            initial_idx = 0

        with col_z2:
            zai_model_select = st.selectbox(
                "GLM Model",
                model_presets,
                index=initial_idx,
                help="Select glm-4.5-air for agentic tasks, or specify a custom model"
            )

        if zai_model_select == "Custom (Specify below)...":
            custom_model_val = st.text_input(
                "Custom Model Identifier",
                value=default_model if default_model not in model_presets[:-1] else "",
                placeholder="e.g. glm-4.5-air",
                help="Enter the exact model code recognized by your ZAI BigModel or Z.ai endpoint"
            )
            zai_model_input = custom_model_val.strip()
        else:
            zai_model_input = zai_model_select

        zai_url_input = st.text_input(
            "API Base URL (OpenAI-compatible)",
            value=default_url,
            help="Default: https://open.bigmodel.cn/api/paas/v4/ (BigModel) or https://api.z.ai/api/paas/v4/ (Z.ai Global)"
        )

        col_btn1, col_btn2 = st.columns([1, 4])
        with col_btn1:
            if st.button("⚡ Test & Save ZAI Key", key="btn_save_zai"):
                if not zai_key_input:
                    st.error("Please enter an API key.")
                elif not zai_model_input:
                    st.error("Please specify a valid model name.")
                else:
                    with st.spinner(f"Connecting to {zai_url_input} with model '{zai_model_input}'..."):
                        ok, msg, latency = asyncio.run(test_zai_connection(
                            api_key=zai_key_input,
                            base_url=zai_url_input,
                            model=zai_model_input
                        ))
                        if ok:
                            VAULT.set_credential("zai", {
                                "api_key": zai_key_input.strip(),
                                "model": zai_model_input.strip(),
                                "base_url": zai_url_input.strip()
                            })
                            st.success(f"✅ {msg}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msg}")

        with col_btn2:
            if zai_creds.get("api_key"):
                st.caption(f"Currently Stored: `{mask_secret(zai_creds.get('api_key'))}`")

    # ------------------ ATLASSIAN JIRA ------------------
    with card_jira:
        st.markdown("### 📌 Atlassian Jira Configuration")
        st.info("Retrieve live sprints, blockers, epics, and execute approval-gated issue creations.")
        
        jira_creds = VAULT.get_credential("jira")
        jira_auth_type = st.radio(
            "Connection Protocol",
            ["Atlassian Cloud REST API (Standard)", "Official Hosted MCP Server (mcp.atlassian.com)"],
            horizontal=True,
            key="wizard_jira_auth_type"
        )

        if "REST API" in jira_auth_type:
            st.link_button("🔗 Open Atlassian API Token Page", "https://id.atlassian.com/manage-profile/security/api-tokens", use_container_width=True)
            with st.expander("📖 Step-by-Step Guide: How to obtain your Atlassian API Token", expanded=False):
                st.markdown("""
                1. Click the button above to log into your Atlassian account security page: [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
                2. Click **Create API token**.
                3. Label it (e.g., `ContextMesh Integration`) and copy the token.
                4. Fill in your Jira instance domain (e.g. `https://your-company.atlassian.net`) and user email below.
                """)
            jira_url = st.text_input("Jira Instance URL", value=jira_creds.get("base_url", ""), placeholder="https://your-company.atlassian.net")
            jira_email = st.text_input("Atlassian User Email", value=jira_creds.get("user_email", ""), placeholder="you@company.com")
            jira_token = st.text_input("Atlassian API Token", value=jira_creds.get("api_token", ""), type="password", placeholder="ATATT3xFfGF0...")

            if st.button("⚡ Test & Save Jira Connection", key="btn_save_jira"):
                with st.spinner("Verifying Jira credentials via Atlassian REST API..."):
                    ok, msg, latency = asyncio.run(test_jira_connection(jira_url, jira_email, jira_token))
                    if ok:
                        data = dict(jira_creds)
                        data.update({
                            "base_url": jira_url,
                            "user_email": jira_email,
                            "api_token": jira_token
                        })
                        VAULT.set_credential("jira", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
        else:
            st.markdown("Connect directly to the official **Atlassian Rovo Model Context Protocol (MCP)** server.")
            st.link_button("🔗 Atlassian MCP Documentation & Portal", "https://support.atlassian.com/atlassian-intelligence/docs/connect-rovo-to-model-context-protocol/", use_container_width=True)
            with st.expander("📖 Atlassian MCP Authentication Details", expanded=False):
                st.markdown("""
                - **Personal API Token**: Requires HTTP Basic Auth (`email` + `token`). Org Admin must enable *Allow API token authentication* in Atlassian Admin.
                - **OAuth 2.1**: Bearer token obtained from Atlassian OAuth redirect flow.
                """)
            jira_mcp_url = st.text_input(
                "Remote MCP Endpoint URL",
                value=jira_creds.get("mcp_endpoint", "https://mcp.atlassian.com/v2/mcp"),
                help="Official Atlassian cloud MCP endpoint (SSE transport)"
            )
            jira_mcp_email = st.text_input(
                "Atlassian User Email (Required for API Token)",
                value=jira_creds.get("user_email", ""),
                placeholder="you@company.com",
                help="Required if using an Atlassian API token for Basic Auth. Leave empty if using OAuth 2.1 Bearer token."
            )
            jira_mcp_token = st.text_input(
                "Atlassian API Token or OAuth Bearer Token",
                value=jira_creds.get("mcp_token", ""),
                type="password",
                placeholder="ATATT... (API Token) or OAuth Bearer token..."
            )
            if st.button("⚡ Test & Save Jira MCP Connection", key="btn_save_jira_mcp"):
                with st.spinner("Connecting to official Atlassian MCP server..."):
                    ok, msg, latency = asyncio.run(test_mcp_connection(
                        service="jira",
                        endpoint_url=jira_mcp_url,
                        auth_token=jira_mcp_token,
                        user_email=jira_mcp_email.strip() if jira_mcp_email else None
                    ))
                    data = dict(jira_creds)
                    data.update({
                        "mcp_endpoint": jira_mcp_url.strip(),
                        "mcp_token": jira_mcp_token.strip()
                    })
                    if jira_mcp_email.strip():
                        data["user_email"] = jira_mcp_email.strip()
                    VAULT.set_credential("jira", data)
                    if ok:
                        st.success(f"✅ {msg}")
                    else:
                        st.warning(f"Saved credentials, but live test returned: {msg}")
                    st.rerun()

    # ------------------ SLACK ------------------
    with card_slack:
        st.markdown("### 💬 Slack Configuration")
        st.info("Query project channels, release updates, canvases, and discussion threads across your Slack workspace.")
        slack_creds = VAULT.get_credential("slack")
        slack_auth_type = st.radio(
            "Connection Protocol",
            ["Slack Bot/User Token (Web API)", "Official Hosted Slack MCP (mcp.slack.com)"],
            horizontal=True,
            key="wizard_slack_auth_type"
        )

        if "Web API" in slack_auth_type:
            st.link_button("🔗 Open Slack API Apps Console", "https://api.slack.com/apps", use_container_width=True)
            with st.expander("📖 Step-by-Step Guide: How to create a Slack App & obtain tokens", expanded=False):
                st.markdown("""
                1. Click the button above to open Slack's App console: [https://api.slack.com/apps](https://api.slack.com/apps)
                2. Click **Create New App** > **From scratch**, name it `ContextMesh`, and select your Slack workspace.
                3. Under **OAuth & Permissions**, add Bot Token Scopes:
                   - `channels:history`, `channels:read` (public channel messages)
                   - `groups:history`, `groups:read` (private channels if needed)
                   - `chat:write` (sending messages/approvals)
                   - `search:read` (workspace search)
                4. Click **Install to Workspace** at the top of the page.
                5. Copy either the **Bot User OAuth Token** (`xoxb-...`) or **User OAuth Token** (`xoxp-...`).
                """)
            default_slack_token = slack_creds.get("bot_token") or slack_creds.get("user_token") or ""
            slack_token = st.text_input("Slack Bot/User Token", value=default_slack_token, type="password", placeholder="xoxb-... or xoxp-...")

            if st.button("⚡ Test & Save Slack Connection", key="btn_save_slack"):
                with st.spinner("Connecting to Slack API..."):
                    ok, msg, latency = asyncio.run(test_slack_connection(slack_token))
                    if ok:
                        data = dict(slack_creds)
                        if slack_token.startswith("xoxp-"):
                            data["user_token"] = slack_token.strip()
                        else:
                            data["bot_token"] = slack_token.strip()
                        VAULT.set_credential("slack", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
        else:
            st.markdown("Connect to the official **Slack Hosted Model Context Protocol (MCP)** server.")
            st.link_button("🔗 Slack MCP Documentation", "https://docs.slack.dev/ai/slack-mcp-server/", use_container_width=True)
            with st.expander("📖 Slack MCP Configuration & Protocol", expanded=False):
                st.markdown("""
                - **Official Hosted MCP (`https://mcp.slack.com/mcp`)**: Runs over JSON-RPC 2.0 via Streamable HTTP.
                - **Authentication**: Requires a Slack User token (`xoxp-...`) or Bot token (`xoxb-...`) passed in the `Authorization: Bearer <token>` header.
                - **Tools**: Supports `slack.search_messages`, `slack.get_thread`, and `slack.post_message`.
                """)
            slack_mcp_url = st.text_input(
                "Remote MCP Endpoint URL",
                value=slack_creds.get("mcp_endpoint", "https://mcp.slack.com/mcp"),
                help="Official hosted Slack MCP endpoint"
            )
            slack_mcp_token = st.text_input(
                "Slack MCP / OAuth Bearer Token",
                value=slack_creds.get("mcp_token", ""),
                type="password",
                placeholder="xoxb-... or xoxp-... or Bearer token"
            )
            if st.button("⚡ Test & Save Slack MCP Connection", key="btn_save_slack_mcp"):
                with st.spinner("Connecting to official Slack MCP server..."):
                    ok, msg, latency = asyncio.run(test_mcp_connection("slack", slack_mcp_url, slack_mcp_token))
                    data = dict(slack_creds)
                    data.update({
                        "mcp_endpoint": slack_mcp_url.strip(),
                        "mcp_token": slack_mcp_token.strip()
                    })
                    VAULT.set_credential("slack", data)
                    if ok:
                        st.success(f"✅ {msg}")
                    else:
                        st.warning(f"Saved credentials, but live test returned: {msg}")
                    st.rerun()

    # ------------------ GMAIL ------------------
    with card_gmail:
        st.markdown("### 📧 Gmail Configuration")
        st.info("Search email threads for stakeholder commitments, release announcements, and approvals.")

        col_gb1, col_gb2 = st.columns(2)
        with col_gb1:
            st.link_button("🔗 Open Google App Passwords Page", "https://myaccount.google.com/apppasswords", use_container_width=True)
        with col_gb2:
            st.link_button("🔗 Open Google OAuth2 Playground", "https://developers.google.com/oauthplayground", use_container_width=True)

        with st.expander("📖 Step-by-Step Guide: Connect with Google App Password (Fastest)", expanded=False):
            st.markdown("""
            1. Click the **Open Google App Passwords Page** button above: [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
               *(Requires 2-Step Verification enabled)*.
            2. Enter an app name (e.g. `ContextMesh`) and click **Create**.
            3. Copy the 16-character password displayed (e.g. `xxxx xxxx xxxx xxxx`).
            4. Enter your Gmail address and the 16-character password below.
            """)

        gmail_creds = VAULT.get_credential("gmail")
        gmail_auth_type = st.radio(
            "Authentication Method",
            [
                "Google App Password (Recommended)",
                "OAuth2 Access Token",
                "Official Google Workspace MCP (gmailmcp.googleapis.com)"
            ],
            horizontal=True,
            key="wizard_gmail_auth_type"
        )

        if "Official Google Workspace MCP" in gmail_auth_type:
            st.link_button("🔗 Google Workspace Gmail MCP Docs", "https://developers.google.com/workspace/gmail/api/reference/mcp", use_container_width=True)
            with st.expander("📖 Google Workspace MCP Auth Note", expanded=False):
                st.markdown("""
                - **Official Gmail MCP (`gmailmcp.googleapis.com`)**: Strictly requires a **Google Cloud OAuth 2.0 Access Token** (`ya29...`) with `gmail.readonly` or `gmail.compose` scope.
                - **Google App Passwords (16 characters)**: Cannot be used with `gmailmcp.googleapis.com`. App Passwords only work for IMAP (`imap.gmail.com`). Use the **Google App Password** mode above if you don't have an OAuth2 token.
                """)
            gmail_mcp_url = st.text_input(
                "Remote MCP Endpoint URL",
                value=gmail_creds.get("mcp_endpoint", "https://gmailmcp.googleapis.com/mcp/v1"),
                help="Official Google Workspace MCP endpoint"
            )
            gmail_mcp_token = st.text_input(
                "Google Workspace OAuth2 Access Token",
                value=gmail_creds.get("mcp_token", ""),
                type="password",
                placeholder="ya29.a0..."
            )
            if st.button("⚡ Test & Save Gmail MCP Connection", key="btn_save_gmail_mcp"):
                with st.spinner("Connecting to official Gmail MCP server..."):
                    ok, msg, _ = asyncio.run(test_mcp_connection("gmail", gmail_mcp_url, gmail_mcp_token))
                    data = dict(gmail_creds)
                    data.update({
                        "mcp_endpoint": gmail_mcp_url.strip(),
                        "mcp_token": gmail_mcp_token.strip()
                    })
                    VAULT.set_credential("gmail", data)
                    if ok:
                        st.success(f"✅ {msg}")
                    else:
                        st.warning(f"Saved credentials, but live test returned: {msg}")
                    st.rerun()
        else:
            gmail_account = st.text_input("Gmail Address", value=gmail_creds.get("account", ""), placeholder="you@company.com", key="wizard_gmail_email")

            if "App Password" in gmail_auth_type:
                gmail_app_pw = st.text_input("16-character App Password", value=gmail_creds.get("app_password", ""), type="password", placeholder="xxxx xxxx xxxx xxxx", key="wizard_gmail_pw")
                gmail_token = None
            else:
                gmail_token = st.text_input("OAuth2 Bearer Access Token", value=gmail_creds.get("access_token", ""), type="password", placeholder="ya29.a0...", key="wizard_gmail_token")
                gmail_app_pw = None

            if st.button("⚡ Test & Save Gmail Connection", key="btn_save_gmail"):
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
                        VAULT.set_credential("gmail", data)
                        st.success(f"✅ {msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")

