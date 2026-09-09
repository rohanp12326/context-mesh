# Authentication & Secure Credential Management Guide

ContextMesh provides an interactive, non-intrusive authentication workflow designed to connect enterprise systems (ZAI GLM, Jira, Notion, Gmail) without requiring hardcoded secrets or raw environment file editing.

---

## 1. Security Architecture: The Credential Vault

All third-party credentials entered through the frontend or API are stored using **Fernet symmetric encryption** (AES-128-CBC with HMAC-SHA256 authenticated encryption):

- **Master Key**: Generated automatically and stored in `.secrets/.vault_key` with strict filesystem permissions (`chmod 0600`).
- **Encrypted Keystore**: Stored in `.secrets/vault.enc`. Raw tokens and API keys are never stored in plaintext on disk.
- **Masked Telemetry**: Keys are masked across all logs, traces, and UI displays (`sk-****...a8b9`).
- **Dynamic Reconfiguration**: Connectors dynamically resolve credentials from the vault on every query. Updating a key in the UI immediately applies to the next agent execution without restarting the server.

---

## 2. Supported Providers & Setup Instructions

### 🤖 2.1 ZAI GLM API (Zhipu AI)
ContextMesh uses ZAI's GLM models (e.g. `glm-4-plus`, `glm-4-flash`) via an OpenAI-compatible interface.
1. Sign up or log into the **Zhipu AI Open Platform**: [https://open.bigmodel.cn/](https://open.bigmodel.cn/)
2. Navigate to **API Keys** in the User Center: [open.bigmodel.cn/usercenter/apikeys](https://open.bigmodel.cn/usercenter/apikeys)
3. Click **Create API Key** and copy the string.
4. In ContextMesh Streamlit UI, navigate to the **"🔐 Integrations & Auth"** tab.
5. Paste your API key, choose your model (`glm-4-plus`), and click **⚡ Test & Save ZAI Key**.

---

### 📌 2.2 Atlassian Jira
1. Log in to your Atlassian account's security portal: [https://id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token**, give it a label (e.g., `ContextMesh`), and copy the token.
3. In the UI, enter:
   - **Jira Instance URL**: `https://your-company.atlassian.net`
   - **User Email**: Your Atlassian account email
   - **API Token**: The token generated in step 2.
4. Click **⚡ Test & Save Jira Connection** to verify with Atlassian's REST API.

---

### 📓 2.3 Notion
1. Go to Notion's Integration Portal: [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **+ New integration**.
3. Set the name to `ContextMesh`, select your workspace, and copy the **Internal Integration Secret** (`ntn_...`).
4. **Granting Page Access**:
   - Open any Notion page, database, or meeting note you want ContextMesh to read.
   - Click the `...` menu in the top-right corner.
   - Click **Connect to** and select your `ContextMesh` integration.
5. In ContextMesh UI, paste the secret into the Notion card and click **⚡ Test & Save Notion Connection**.

---

### 📧 2.4 Gmail (Google Workspace)
1. Go to the **Google Cloud Console**: [https://console.cloud.google.com/](https://console.cloud.google.com/)
2. Enable the **Gmail API** under APIs & Services.
3. Create an OAuth 2.0 Client ID (Desktop app) or configure App Passwords for your organization.
4. Save the client credentials to `secrets/gmail_credentials.json` or configure the account email in the UI.

---

## 3. Switching Modes: Demo Sandbox vs. Live Enterprise

ContextMesh includes a one-click mode switcher on the **"🔐 Integrations & Auth"** tab:
- **🧪 Demo Sandbox (Synthetic Project Atlas)**: Perfect for offline development, evaluations, and testing. Queries synthetic Jira tickets, Notion runbooks, and email threads without connecting to live external servers.
- **🚀 Live Enterprise Mode**: Directs the agent to query your live authenticated enterprise APIs.
