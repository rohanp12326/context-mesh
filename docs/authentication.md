# Authentication & Secure Credential Management Guide

ContextMesh provides an interactive, non-intrusive authentication workflow designed to connect enterprise systems (ZAI GLM, Jira, Slack, Gmail) without requiring hardcoded secrets or raw environment file editing.

---

## 1. Security Architecture: The Credential Vault

All third-party credentials entered through the frontend or API are stored using **Fernet symmetric encryption** (AES-128-CBC with HMAC-SHA256 authenticated encryption):
- The encryption key is derived and saved to `.secrets/vault.key`.
- The encrypted payload is persisted to `.secrets/vault.enc`.
- The `.secrets/` directory is strictly excluded in `.gitignore` to guarantee credentials are never checked into version control.

---

## 2. Setting Up Live Enterprise Integrations

### 🤖 2.1 ZAI GLM API (Foundation LLM)
1. Open the **Zhipu BigModel Console** ([open.bigmodel.cn/usercenter/apikeys](https://open.bigmodel.cn/usercenter/apikeys)) or the global **Z.ai Platform** ([z.ai](https://z.ai/)).
2. Copy your API Key (e.g. `74xxxxxxxxxx.xxxxxxxxxxxx`).
3. In ContextMesh, open the **"🔐 Integrations & Auth"** tab and paste the key.
4. Select your preferred model (default: `glm-4.5-air` for agent workflows).
5. Click **⚡ Test & Save ZAI Connection**.

---

### 📌 2.2 Atlassian Jira
1. Go to Atlassian API Tokens: [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**, name it `ContextMesh`, and copy the token.
3. In ContextMesh, provide:
   - **Base URL**: e.g., `https://your-company.atlassian.net`
   - **User Email**: Your Atlassian account email
   - **API Token**: The token generated in step 2.
4. Click **⚡ Test & Save Jira Connection** to verify with Atlassian's REST API.

---

### 💬 2.3 Slack
1. Go to Slack's App Management Console: [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App** > **From scratch**, name it `ContextMesh`, and select your workspace.
3. Under **OAuth & Permissions**, add Bot Token Scopes:
   - `channels:history`, `channels:read`
   - `groups:history`, `groups:read`
   - `chat:write`
   - `search:read`
4. Click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`) or **User OAuth Token** (`xoxp-...`).
5. In ContextMesh UI, paste the token into the Slack card and click **⚡ Test & Save Slack Connection**.

Alternatively, connect to the official hosted **Slack MCP Server** at `https://mcp.slack.com/mcp` using your OAuth Bearer / Bot token.

---

### 📧 2.4 Gmail (Google Workspace)

ContextMesh supports two connection methods for Gmail:

#### Method A: Google App Password (Recommended & Easiest for Non-Technical Users)
1. Ensure **2-Step Verification** is turned on in your Google Account: [myaccount.google.com/signinoptions/two-step-verification](https://myaccount.google.com/signinoptions/two-step-verification)
2. Go directly to **Google App Passwords**: [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Type an app name (e.g., `ContextMesh`) in the box and click **Create**.
4. Google will display a 16-character passcode (e.g., `abcd efgh ijkl mnop`). Copy it.
5. In ContextMesh (either in the popup modal or the "🔐 Integrations & Auth" tab):
   - Enter your **Gmail Address**.
   - Paste the **16-character App Password**.
   - Click **⚡ Test & Connect Gmail**.

#### Method B: OAuth2 Bearer Token (For Developers & Cloud Administrators)
1. Go to the **Google OAuth2 Playground**: [https://developers.google.com/oauthplayground](https://developers.google.com/oauthplayground)
2. In Step 1, select `Gmail API v1` -> `https://mail.google.com/`.
3. Click **Authorize APIs** and log into your account.
4. In Step 2, click **Exchange authorization code for tokens**.
5. Copy the generated **Access token** (`ya29.a0...`) and paste it into the UI.

---

## 3. Strict Live Enterprise Operation

ContextMesh is configured strictly for **Live Enterprise Mode**:
- **🚀 Live Enterprise Mode**: Directs the agent to query your live authenticated enterprise APIs via Composio MCP gateway or direct provider credentials.
- When an integration is unconfigured, the agent returns empty results or prompts the user with an authentication link—never falling back to synthetic mock data.
