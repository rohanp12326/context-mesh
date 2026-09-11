# ContextMesh Architecture & Technical Design

## 1. System Overview

**ContextMesh** is an enterprise AI engineering intelligence assistant designed to resolve cross-system questions spanning Jira, Slack, and Gmail. Instead of naive vector search over chunked documents, ContextMesh relies on **typed query decomposition**, **parallel tool retrieval via Model Context Protocol (MCP)**, **evidence normalization & freshness resolution**, **tiered memory**, and **approval-gated writes**.

```text
User / Streamlit UI / API
          |
          v
 FastAPI Application Layer (/api/v1/chat)
          |
          v
 Intent & Risk Policy Engine (Read vs Mutate Check)
          |
          v
 Query Planner (LLM: ZAI GLM / glm-4-plus)
   ├── Resolves Durable Project Aliases (e.g. Atlas -> ATL)
   └── Produces Typed Execution Plan
          |
          v
 Parallel MCP Tool Execution
    ├── Jira MCP Server  ──> JQL issue search & blocker checks
    ├── Slack MCP Server ──> Channel messages, canvases, & thread discussions
    └── Gmail MCP Server ──> Email thread & commitment extraction
          |
          v
 Evidence Normalization & Freshness Layer
    ├── SHA-256 Content Hashing & Metadata Canonicalization
    ├── Timestamp Freshness Calculation (<24h, recent, stale)
    └── Cross-Source Contradiction Detection
          |
          v
 Evidence-Backed Answer Synthesizer
    ├── Claim-Level Citations [jira:ATL-101], [gmail:th-01]
    └── Conflict & Freshness Alerts
          |
          v
 Tiered Memory Update & Candidate Promotion
    ├── Thread-Scoped Working State Checkpoint
    └── Long-Term Structured Memory Promotion (Superseding Rules)
```

---

## 2. Core Components

### 2.1 Model Context Protocol (MCP) Integration
- Standardized tool boundaries decoupling connectors from the agent core.
- Read operations (`jira.search_issues`, `slack.search_messages`, `gmail.search_messages`) run in parallel via `asyncio.gather`.
- Mutation operations (`jira.create_issue`) are flagged with `requires_approval = True`.

### 2.2 Tiered Memory Subsystem
- **Short-Term Memory**: Conversation thread checkpoints storing rolling message summaries, active plans, and pending human approvals.
- **Long-Term Memory**: Structured, cross-session storage for:
  - Project aliases (`Atlas` -> `ATL`, `PaymentsV2` -> `PAY`)
  - People and role mappings (leads, domains, emails)
  - Confirmed architectural decisions
- **Promotion & Superseding Engine**:
  - Automatically rejects transient sprint statuses, passwords, and sensitive keys.
  - When an alias or decision changes, the older memory is marked with `superseded_by` rather than silently deleted, preserving provenance.

### 2.3 Evidence Normalization & Freshness
Every piece of retrieved data is normalized into canonical `Evidence`:
- `source`: `"jira" | "slack" | "gmail" | "memory"`
- `source_object_id`: e.g. `ATL-101`, `gmail-th-01`
- `source_url`: Clickable reference
- `content_hash`: Sha256 integrity hash
- `updated_at` / `fetched_at`: Timestamps used to compute freshness labels (`Live (<24h)`, `Recent`, `Potentially Stale`).
- **Contradiction Engine**: Identifies discrepancies (e.g. Slack target release spec vs Gmail delay announcements).
