"""Interactive Authentication Wizard and Credential Vault UI for Streamlit."""

import asyncio
import os
import streamlit as st

from security.vault import VAULT, mask_secret
from security.connection_testers import (
    test_zai_connection,
    test_jira_connection,
    test_notion_connection,
    test_gmail_connection
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
            render_status_pill(status["notion"]["is_configured"], "Notion")
        with c4:
            render_status_pill(status["gmail"]["is_configured"], "Gmail")

    st.markdown("---")

    # 2. Service Cards
    card_zai, card_jira, card_notion, card_gmail = st.tabs([
        "🤖 ZAI GLM API",
        "📌 Atlassian Jira",
        "📓 Notion",
        "📧 Gmail"
    ])

    # ------------------ ZAI GLM API ------------------
    with card_zai:
        st.markdown("### 🤖 ZAI GLM API Configuration")
        st.info(
            "ContextMesh uses ZAI's GLM foundation models for query decomposition, risk analysis, and cited answer synthesis."
        )

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

        with st.expander("📖 Step-by-Step Guide: How to obtain your Atlassian API Token", expanded=False):
            st.markdown("""
            1. Log in to your Atlassian account security page: [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
            2. Click **Create API token**.
            3. Label it (e.g., `ContextMesh Integration`) and copy the token.
            4. Fill in your Jira instance domain (e.g. `https://your-company.atlassian.net`) and user email below.
            """)

        jira_creds = VAULT.get_credential("jira")
        jira_url = st.text_input("Jira Instance URL", value=jira_creds.get("base_url", ""), placeholder="https://your-company.atlassian.net")
        jira_email = st.text_input("Atlassian User Email", value=jira_creds.get("user_email", ""), placeholder="you@company.com")
        jira_token = st.text_input("Atlassian API Token", value=jira_creds.get("api_token", ""), type="password", placeholder="ATATT3xFfGF0...")

        if st.button("⚡ Test & Save Jira Connection", key="btn_save_jira"):
            with st.spinner("Verifying Jira credentials via Atlassian REST API..."):
                ok, msg, latency = asyncio.run(test_jira_connection(jira_url, jira_email, jira_token))
                if ok:
                    VAULT.set_credential("jira", {
                        "base_url": jira_url,
                        "user_email": jira_email,
                        "api_token": jira_token
                    })
                    st.success(f"✅ {msg}")
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")

    # ------------------ NOTION ------------------
    with card_notion:
        st.markdown("### 📓 Notion Configuration")
        st.info("Query project specs, architecture decision records, and meeting runbooks.")

        with st.expander("📖 Step-by-Step Guide: How to set up Notion Internal Integration", expanded=False):
            st.markdown("""
            1. Open Notion's integration manager: [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
            2. Click **New integration**.
            3. Name it `ContextMesh`, associate it with your workspace, and copy the **Internal Integration Secret** (`ntn_...`).
            4. **Crucial Step**: In your Notion app, open any page or database you want the agent to see, click the `...` menu in the top right, select **Connect to**, and choose your `ContextMesh` integration.
            """)

        notion_creds = VAULT.get_credential("notion")
        notion_token = st.text_input("Notion Integration Secret", value=notion_creds.get("api_key", ""), type="password", placeholder="ntn_...")

        if st.button("⚡ Test & Save Notion Connection", key="btn_save_notion"):
            with st.spinner("Connecting to Notion API..."):
                ok, msg, latency = asyncio.run(test_notion_connection(notion_token))
                if ok:
                    VAULT.set_credential("notion", {"api_key": notion_token})
                    st.success(f"✅ {msg}")
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")

    # ------------------ GMAIL ------------------
    with card_gmail:
        st.markdown("### 📧 Gmail Configuration")
        st.info("Search email threads for stakeholder commitments, release announcements, and approvals.")

        with st.expander("📖 Step-by-Step Guide: Google Workspace OAuth Setup", expanded=False):
            st.markdown("""
            1. Visit **Google Cloud Console**: [https://console.cloud.google.com/](https://console.cloud.google.com/)
            2. Create a Project and enable the **Gmail API**.
            3. Configure the OAuth Consent Screen and create OAuth 2.0 Client Credentials (`credentials.json`).
            4. Alternatively for Google Workspace, configure App Passwords or place your credentials file in `secrets/gmail_credentials.json`.
            """)

        gmail_creds = VAULT.get_credential("gmail")
        gmail_account = st.text_input("Authorized Gmail Account", value=gmail_creds.get("account", ""), placeholder="you@company.com")
        gmail_file = st.text_input("Path to credentials.json (optional)", value=gmail_creds.get("credentials_file", "secrets/gmail_credentials.json"))

        if st.button("⚡ Save Gmail Configuration", key="btn_save_gmail"):
            ok, msg, _ = asyncio.run(test_gmail_connection(gmail_account))
            if ok:
                VAULT.set_credential("gmail", {
                    "account": gmail_account,
                    "credentials_file": gmail_file
                })
                st.success("✅ Gmail configuration saved!")
                st.rerun()
            else:
                st.error(f"❌ {msg}")
