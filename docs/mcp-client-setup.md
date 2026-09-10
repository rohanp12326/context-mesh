# Model Context Protocol (MCP) Client Setup Guide

This guide explains how to connect external MCP clients (such as **Claude Desktop**, **Cursor IDE**, **Windsurf**, or **VS Code Cline/Roo**) to ContextMesh.

ContextMesh implements standard Model Context Protocol (MCP) servers over **`stdio`** (standard subprocess transport) and **`sse`** (Server-Sent Events / HTTP transport), exposing both low-level enterprise tools (Jira, Notion, Gmail) and the high-level cross-system reasoning agent (`contextmesh_query`).

---

## 1. Exposed MCP Tools

When connected to ContextMesh, your MCP client gains access to the following tools:

| Tool Name | Parameters | Description |
|---|---|---|
| `contextmesh_query` | `query: str`, `user_id: str` | **Full cross-tool agent**: Decomposes inquiries, queries Jira/Notion/Gmail concurrently, detects contradictions, and synthesizes cited answers. |
| `jira_search_issues` | `query: str`, `limit: int` | Search Jira issues using JQL syntax or search terms (e.g. `project = ATL AND statusCategory != Done`). |
| `jira_get_issue` | `issue_key: str` | Fetch complete details and blocker status for an issue (e.g. `ATL-101`). |
| `jira_create_issue` | `project: str`, `summary: str`, `description: str`, `priority: str` | Create a new issue/task in Jira (requires approval/token). |
| `notion_search_pages` | `query: str`, `limit: int` | Search Notion pages, architecture specifications, runbooks, and meeting notes. |
| `notion_get_page_content` | `page_id: str` | Fetch markdown content of a specific Notion document by ID. |
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
        "NOTION_API_KEY": "ntn_your_notion_secret",
        "ZAI_API_KEY": "your_zai_glm_api_key",
        "CONNECTOR_MODE": "live"
      }
    }
  }
}
```
*(Replace `/ABSOLUTE/PATH/TO/context-mesh` with the actual path to your repository).*

> [!TIP]
> To use offline synthetic demo data (Project Atlas sandbox) without configuring live enterprise API keys, set `"CONNECTOR_MODE": "mock"`.

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

## 3. Official Provider MCP Servers (Google, Atlassian & Notion)

Each major enterprise SaaS provider now offers official, first-party Model Context Protocol (MCP) servers:

### 3.1 Overview of Official Hosted MCP Servers & Authentication Requirements

| Provider | Official MCP Endpoint | Official Documentation | Auth Method & Header Format | Accepted Credentials | Crucial Notes / Gotchas |
|---|---|---|---|---|---|
| **Google** | `https://gmailmcp.googleapis.com/mcp/v1` | [Gmail MCP Reference](https://developers.google.com/workspace/gmail/api/reference/mcp) | **Bearer Auth**<br>`Authorization: Bearer <token>` | Google Cloud OAuth 2.0 Access Token (`ya29...`) with `gmail.readonly` or `gmail.compose` scope | ⚠️ **Google App Passwords (16 chars) DO NOT WORK** with `gmailmcp.googleapis.com`. App Passwords are strictly for IMAP (`imap.gmail.com`). |
| **Atlassian** | `https://mcp.atlassian.com/v2/mcp` | [Atlassian Rovo MCP](https://support.atlassian.com/atlassian-intelligence/docs/connect-rovo-to-model-context-protocol/) | **OAuth 2.1**: `Authorization: Bearer <token>`<br>**Basic Auth (API Token)**: `Authorization: Basic base64(email:token)` | Atlassian OAuth 2.1 token OR Personal API Token (`ATATT...`) with user email | ⚠️ Atlassian Org Admin must enable **"Allow API token authentication"** in *Atlassian Admin > Rovo > Rovo MCP server > Authentication*. Without this, API tokens will fail. |
| **Notion** | `https://mcp.notion.com/mcp` | [Notion MCP Docs](https://developers.notion.com/) | **OAuth 2.0 PKCE**<br>`Authorization: Bearer <token>` | Notion OAuth 2.0 access token | ⚠️ **Internal Integration Secrets (`ntn_...`) DO NOT WORK** directly with `mcp.notion.com/mcp` (returns 401). Internal secrets work with Notion's REST API or local stdio `@modelcontextprotocol/server-notion`. |

---

### 3.2 Single-App Official Servers vs. ContextMesh Multi-App Mesh

- **Single-App Official Servers**:
  - Hosted directly in the provider's cloud.
  - Excellent for interacting with one platform in isolation (e.g. asking Claude to search Jira or search Gmail).
  - **Limitations**: Each server operates in a silo. They cannot detect cross-system discrepancies (e.g., Jira showing a delayed launch date that conflicts with a Notion spec or an email sign-off).
- **ContextMesh Unified Mesh (`context-mesh`)**:
  - Acts as the **orchestrator and synthesis layer** across Jira, Notion, and Gmail.
  - Automatically decomposes queries, retrieves evidence across all three systems in parallel, detects temporal contradictions, calculates SHA-256 evidence integrity hashes, and generates grounded bracket citations.

---

### 3.3 Connecting Claude Desktop to All Official MCP Servers

You can connect Claude Desktop simultaneously to Google Gmail, Atlassian Jira, Notion, and ContextMesh in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "google-gmail": {
      "url": "https://gmailmcp.googleapis.com/mcp/v1",
      "oauth": {
        "clientId": "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com",
        "clientSecret": "YOUR_GOOGLE_CLIENT_SECRET"
      }
    },
    "atlassian-jira": {
      "url": "https://mcp.atlassian.com/v2/mcp"
    },
    "notion": {
      "url": "https://mcp.notion.com/mcp"
    },
    "context-mesh": {
      "command": "/path/to/context-mesh/.venv/bin/python",
      "args": ["-m", "mcp_servers.server"],
      "env": {
        "PYTHONPATH": "/path/to/context-mesh"
      }
    }
  }
}
```

When Claude Desktop launches:
- **Google Gmail**: Prompts to sign in via Google Cloud OAuth.
- **Atlassian Jira**: Prompts to sign in via Atlassian Cloud OAuth 2.1.
- **Notion**: Prompts to authorize your Notion workspace.
- **ContextMesh**: Runs locally via `stdio` to provide cross-tool reasoning and offline demo testing.

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
  - Task queries (*"what are my opened tasks"*) -> Jira + Notion.
  - General queries (*"who is the president of america"*, *"how to setup windows 11"*) -> Answered directly with 0 tool calls.
