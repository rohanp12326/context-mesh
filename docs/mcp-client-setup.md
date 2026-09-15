# Model Context Protocol (MCP) Client Setup Guide

This guide explains how to connect external MCP clients (such as **Claude Desktop**, **Cursor IDE**, **Windsurf**, or **VS Code Cline/Roo**) to ContextMesh.

ContextMesh implements standard Model Context Protocol (MCP) servers over **`stdio`** (standard subprocess transport) and **`sse`** (Server-Sent Events / HTTP transport), exposing both low-level enterprise tools (Jira, Slack, Gmail) and the high-level cross-system reasoning agent (`contextmesh_query`).

---

## 1. Exposed MCP Tools

When connected to ContextMesh, your MCP client gains access to the following tools:

| Tool Name | Parameters | Description |
|---|---|---|
| `contextmesh_query` | `query: str`, `user_id: str` | **Full cross-tool agent**: Decomposes inquiries, queries Jira/Slack/Gmail concurrently, detects contradictions, and synthesizes cited answers. |
| `jira_search_issues` | `query: str`, `limit: int` | Search Jira issues using JQL syntax or search terms (e.g. `project = ATL AND statusCategory != Done`). |
| `jira_get_issue` | `issue_key: str` | Fetch complete details and blocker status for an issue (e.g. `ATL-101`). |
| `jira_create_issue` | `project: str`, `summary: str`, `description: str`, `priority: str` | Create a new issue/task in Jira (requires approval/token). |
| `slack_search_messages` | `query: str`, `limit: int` | Search Slack messages, channels, and canvases for release coordination and discussion. |
| `slack_get_thread` | `channel: str`, `thread_ts: str` | Fetch all replies in a Slack thread by timestamp. |
| `slack_post_message` | `channel: str`, `text: str` | Post a message to a Slack channel (requires approval). |
| `gmail_search_messages` | `query: str`, `limit: int` | Search email threads and messages for stakeholder commitments and discussions. |
| `gmail_get_thread` | `thread_id: str` | Fetch all messages in a specific email thread by thread ID. |

---

## 2. Client Setup Instructions

### 🤖 2.1 Claude Desktop

1. Open or create the Claude Desktop configuration file:
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
   - **Linux**: `~/.config/Claude/claude_desktop_config.json`

2. Add `context-mesh` to the `mcpServers` object:

```json
{
  "mcpServers": {
    "context-mesh": {
      "command": "/ABSOLUTE/PATH/TO/context-mesh/.venv/bin/python",
      "args": ["-m", "mcp_servers.server"],
      "env": {
        "PYTHONPATH": "/ABSOLUTE/PATH/TO/context-mesh",
        "JIRA_URL": "https://your-company.atlassian.net",
        "JIRA_EMAIL": "your-email@company.com",
        "JIRA_API_TOKEN": "your_jira_api_token",
        "SLACK_BOT_TOKEN": "xoxb-your-slack-token",
        "ZAI_API_KEY": "your_zai_glm_api_key",
        "CONNECTOR_MODE": "live"
      }
    }
  }
}
```
*(Replace `/ABSOLUTE/PATH/TO/context-mesh` with the actual path to your repository).*

> [!NOTE]
> ContextMesh operates strictly on live enterprise connectors (`CONNECTOR_MODE=live`). Ensure your `COMPOSIO_API_KEY` or direct service credentials are configured.

3. Restart Claude Desktop. You will see a hammer icon indicating that the 8 ContextMesh tools are active and ready.

---

### 💻 2.2 Cursor IDE

1. In your project workspace, create or edit `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "context-mesh": {
      "command": "${workspaceFolder}/.venv/bin/python",
      "args": ["-m", "mcp_servers.server"],
      "env": {
        "PYTHONPATH": "${workspaceFolder}"
      }
    }
  }
}
```

2. Open Cursor Settings -> **Features** -> **MCP**.
3. Verify that `context-mesh` appears with a green status light and lists the 8 tools.

---

### 🌐 2.3 Continue.dev / Windsurf / VS Code (Cline & Roo-Code)

For VS Code extensions supporting MCP:

```json
{
  "mcpServers": {
    "context-mesh": {
      "command": "python",
      "args": ["-m", "mcp_servers.server"],
      "cwd": "/ABSOLUTE/PATH/TO/context-mesh"
    }
  }
}
```

---

## 3. Streamlined Authentication via Composio MCP Gateway

ContextMesh integrates with the **Composio MCP Gateway** to eliminate fragmented, manual credential generation across enterprise tools. Instead of requiring developers and end-users to register Google Cloud OAuth apps, configure Slack bot scopes, or request Atlassian API tokens, Composio provides a unified 1-click OAuth authentication flow and managed Model Context Protocol server sessions.

### 3.1 Why Composio MCP?

| Feature | Legacy / Manual Provider MCP | Composio MCP Gateway |
|---|---|---|
| **Authentication Flow** | Manual OAuth app creation, redirect URI whitelisting, Atlassian API tokens, Slack bot scopes | **1-Click Hosted OAuth** via `connected_accounts.link` |
| **Credentials Required** | 3+ distinct secrets (Google Client ID/Secret, Atlassian token, Slack `xoxb` token) | **Single Master Key**: `COMPOSIO_API_KEY` |
| **Protocol Transport** | Fragmented (Google Bearer token, Atlassian Basic/OAuth 2.1, Slack HTTP JSON-RPC) | **Unified MCP Session** (SSE / Streamable HTTP) & direct tool fallback |
| **Tool Catalog** | Separate non-standard schemas per provider | Normalized enterprise tool catalog (`JIRA_*`, `SLACK_*`, `GMAIL_*`) |
| **Dynamic JIT Auth** | Silent failure or manual config modal | ContextMesh prompts user with a 1-click OAuth link when a service is required |

---

### 3.2 How Composio MCP Works in ContextMesh

1. **Master Key Configuration**:
   - Store `COMPOSIO_API_KEY` in your `.env` or configure it securely in the ContextMesh UI (**Settings > Integrations > Composio MCP Gateway**).
2. **1-Click App Linking**:
   - The user clicks **Connect Jira**, **Connect Slack**, or **Connect Gmail**.
   - ContextMesh calls the Composio API (`POST /api/v3.1/connected_accounts/link` or Python SDK `client.connected_accounts.link(user_id=..., auth_config_id=...)`) to generate a secure OAuth authorization URL.
   - The user completes authorization in their browser.
3. **Session & Tool Execution**:
   - ContextMesh provisions an active MCP session via `POST /api/v3.1/sessions` with `{"mcp": true}`.
   - Calls are routed through the session's SSE endpoint or executed directly via Composio's tool dispatcher (`POST /api/v3.1/tools/execute`).
   - Action mappings automatically translate ContextMesh requests to Composio actions:
     - `jira_search_issues` -> `JIRA_SEARCH_ISSUES_USING_JQL`
     - `jira_get_issue` -> `JIRA_GET_ISSUE`
     - `jira_create_issue` -> `JIRA_CREATE_ISSUE`
     - `slack_search_messages` -> `SLACK_SEARCH_MESSAGES`
     - `slack_get_thread` -> `SLACK_GET_CONVERSATION_REPLIES`
     - `slack_post_message` -> `SLACK_POST_A_MESSAGE`
     - `gmail_search_messages` -> `GMAIL_FETCH_EMAILS`
     - `gmail_get_thread` -> `GMAIL_GET_THREAD`
4. **Strict Real Data Architecture**:
   - ContextMesh always operates strictly on real data (`mode="live"`). If an integration is unauthenticated or the network is unavailable, it returns empty result sets or prompts for authentication—never falling back to synthetic mock data.

---

### 3.3 Individual Provider Hosted Endpoints (Reference)

For organizations that strictly mandate direct provider-hosted MCP connections without an intermediary gateway, ContextMesh also maintains backward compatibility with individual provider endpoints:

| Provider | Hosted MCP Endpoint | Auth Method & Header Format | Accepted Credentials |
|---|---|---|---|
| **Google** | `https://gmailmcp.googleapis.com/mcp/v1` | Bearer Token (`Authorization: Bearer <token>`) | Google Cloud OAuth 2.0 Access Token (`ya29...`) with `gmail.readonly`/`gmail.compose` |
| **Atlassian** | `https://mcp.atlassian.com/v2/mcp` | Basic / Bearer Auth | Atlassian OAuth 2.1 token or API Token (`ATATT...`) with user email |
| **Slack** | `https://mcp.slack.com/mcp` | Bearer Token (`Authorization: Bearer <token>`) | Slack Bot token (`xoxb-...`) or User token (`xoxp-...`) |

---

### 3.4 Connecting External Clients (Claude Desktop, Cursor) to ContextMesh

External agents connect to the **ContextMesh Unified Mesh**, which orchestrates between Composio MCP, direct REST APIs, and durable memory:

```json
{
  "mcpServers": {
    "context-mesh": {
      "command": "/path/to/context-mesh/.venv/bin/python",
      "args": ["-m", "mcp_servers.server"],
      "env": {
        "PYTHONPATH": "/path/to/context-mesh",
        "COMPOSIO_API_KEY": "your_composio_api_key",
        "CONNECTOR_MODE": "live"
      }
    }
  }
}
```

---

## 4. Testing with the MCP Inspector

You can interactively test and debug the ContextMesh MCP server using the official Anthropic MCP Inspector:

```bash
# In the context-mesh root directory:
source .venv/bin/activate

npx @modelcontextprotocol/inspector python -m mcp_servers.server
```

This launches a local web interface (usually at `http://localhost:5173`) allowing you to:
- Inspect all registered tool signatures and JSON schemas.
- Execute tools with test arguments and view real JSON-RPC responses.
- Verify connector responses before connecting desktop clients.

---

## 4. Running ContextMesh as an SSE (HTTP) Server

If your client runs on a different machine or in Docker, you can run the server over Server-Sent Events (SSE):

```bash
python -m mcp_servers.server --transport sse
```

Configure your remote client to connect to `http://<host>:8000/sse`.

---

## 5. Troubleshooting & FAQ

#### Q: Claude Desktop shows "Failed to connect to MCP server".
- Check that the path to `.venv/bin/python` in `claude_desktop_config.json` is an **absolute path**, not a relative path or `~`.
- Ensure `PYTHONPATH` points to the `context-mesh` root directory.
- Check the ContextMesh log file at `logs/context_mesh.log` for initialization errors.

#### Q: How does ContextMesh know when to query apps?
- ContextMesh uses an **App Determination Engine** (`agent.router.AppRouter`):
  - Email queries (*"what are my recent mails"*) -> Gmail only.
  - Task queries (*"what are my opened tasks"*) -> Jira + Slack.
  - General queries (*"who is the president of america"*, *"how to setup windows 11"*) -> Answered directly with 0 tool calls.
