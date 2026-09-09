A strong version of this project would be an **AI engineering intelligence assistant** that answers cross-tool questions using live Jira, Notion, and Gmail data while remembering durable user and project context.

Call it **ProjectLens** or **OrgMind**.

> “What is blocking the authentication release, who owns each blocker, and what commitments were made in email?”

The agent would decompose this into separate searches, retrieve current evidence from each system, reconcile conflicts and produce a cited answer—not merely search a vector database.

## 1. What makes this resume-worthy

A basic implementation would connect three APIs to an LLM. That is no longer especially distinctive.

The impressive version demonstrates that you can solve the difficult engineering problems surrounding agents:

- Reliable query planning and tool selection
- Live retrieval from heterogeneous systems
- Explicit short-term and long-term memory
- Source citations and freshness indicators
- Permission-aware access
- Failure recovery and human approval
- Trace-based debugging
- Quantitative evaluation—not just a polished demo

The strongest positioning is:

> A permission-aware agentic retrieval system for answering cross-tool organizational questions using live enterprise data, persistent memory, evidence-backed synthesis, and end-to-end evaluation.

## 2. Recommended product scenario

Build around a fictional software team. Populate:

- **Jira:** epics, issues, owners, statuses, blockers and deadlines
- **Notion:** project specifications, meeting notes, architecture decisions and runbooks
- **Gmail:** stakeholder conversations, approvals and delivery commitments

Example questions:

1. “Why was the payments launch delayed?”
2. “Which open Jira items conflict with the launch plan in Notion?”
3. “What did Priya commit to completing this week?”
4. “Summarize Project Atlas since my last session.”
5. “Find decisions discussed in email but never documented in Notion.”
6. “Create a proposed Jira task from the unresolved action item.”  
   The final one should require user approval before performing the write.

These questions force the agent to reason across time, identities, formats and sources.

## 3. Proposed architecture

```text
User / Web UI
      |
      v
Agent API + Authentication
      |
      v
Intent and Risk Classifier
      |
      v
Query Planner
  "Find blockers" → Jira search
  "Find specification" → Notion retrieval
  "Find commitments" → Gmail search
      |
      v
Parallel Tool Execution through MCP
      |
      +-------------+--------------+
      |             |              |
   Jira MCP      Notion MCP     Gmail MCP
      |             |              |
      +-------------+--------------+
                    |
                    v
        Evidence normalization layer
                    |
                    v
   Reranking + conflict/freshness resolution
                    |
                    v
       Evidence-backed answer generator
                    |
                    v
 Answer + citations + trace + confidence
```

Memory is a separate subsystem:

```text
Working state       Conversation memory       Durable memory
current plan        thread summary            user preferences
tool results        unresolved references     project aliases
temporary facts     recent decisions          verified relationships
checkpointer        session database           structured store
```

MCP is a good fit because it separates the agent from individual integrations and provides standardized **tools**, **resources**, and **prompts**. Tools are model-controlled operations, while resources expose contextual data. This lets you replace a custom Jira connector with another compliant server without rewriting the planner. [MCP server concepts](https://modelcontextprotocol.io/specification/2025-06-18/server/index)

## 4. Query decomposition

Do not let the model immediately call arbitrary tools. Give it a planning stage that generates a typed plan.

For example:

```json
{
  "user_intent": "release_risk_summary",
  "entities": {
    "project": "Atlas",
    "time_range": "current sprint"
  },
  "steps": [
    {
      "id": "s1",
      "tool": "jira.search_issues",
      "query": "project = ATLAS AND statusCategory != Done",
      "purpose": "Find unresolved work"
    },
    {
      "id": "s2",
      "tool": "notion.search_pages",
      "query": "Atlas launch plan",
      "purpose": "Find agreed release scope"
    },
    {
      "id": "s3",
      "tool": "gmail.search_messages",
      "query": "\"Atlas\" newer_than:30d",
      "purpose": "Find recent commitments"
    }
  ],
  "dependencies": {
    "s3": ["s1"]
  }
}
```

Your graph should contain nodes such as:

1. Resolve remembered entities
2. Classify intent and risk
3. Generate plan
4. Validate plan
5. Execute independent searches concurrently
6. Inspect whether evidence is sufficient
7. Reformulate unsuccessful searches
8. Normalize and rerank evidence
9. Detect contradictions
10. Compose a cited answer
11. Extract candidate memories
12. Apply memory-writing policy

Add limits like:

- Maximum planning iterations
- Maximum tool calls
- Per-tool timeout
- Token and cost budget
- Duplicate-query detection
- Read/write permission checks

This prevents an agent from looping indefinitely or performing an unintended action.

## 5. Retrieval strategy

This is not traditional “embed every document and retrieve chunks” RAG. Use two retrieval paths.

### Live retrieval

Query source systems whenever current state matters:

- Jira issue status, assignee and sprint
- Recent email
- Recently edited Notion pages
- Anything explicitly requested as “current,” “latest” or “today”

Jira’s API supports JQL-based issue search, making it appropriate for structured filters such as project, assignee, sprint and status. [Jira issue-search API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/)

Gmail supports advanced mailbox queries through `messages.list` and `threads.list`; the listing response contains identifiers, after which message contents must be fetched separately. [Gmail filtering guide](https://developers.google.com/workspace/gmail/api/guides/filtering), [messages.list reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)

Notion requires more care: its general search is optimized primarily for page and data-source titles, not arbitrary full-text retrieval. For reliable content retrieval, find candidate pages and then fetch their block contents, or query a known data source directly. [Notion search limitations](https://developers.notion.com/reference/search-optimizations-and-limitations), [Notion search API](https://developers.notion.com/reference/post-search)

### Cached semantic retrieval

Maintain a local derived index for:

- Previously fetched Notion content
- Email snippets permitted for indexing
- Historical Jira descriptions and comments
- Stable project terminology

Every cached record should include:

```text
source
source_object_id
source_url
updated_at
fetched_at
access_scope
content_hash
embedding_version
```

Before using cached evidence, compare its timestamp with the source or mark it visibly as potentially stale. The source system remains authoritative.

## 6. Memory design

This is where your project can become genuinely differentiated.

### Short-term memory

Scope: one conversation or task.

Store:

- Messages
- Current plan
- Tool outputs or compact references to them
- Resolved entities
- Pending approvals
- A rolling conversation summary

Use a LangGraph checkpointer backed by PostgreSQL. LangGraph explicitly distinguishes thread-scoped state stored by a checkpointer from cross-thread knowledge stored in a separate store. [LangGraph persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence)

Do not retain every raw tool response in the prompt. Persist it externally and insert only the necessary evidence.

### Long-term memory

Scope: shared across sessions for a particular user or organization.

Use structured memory categories:

```json
{
  "memory_id": "uuid",
  "namespace": ["user-17", "project-atlas"],
  "type": "project_alias",
  "content": {
    "alias": "Atlas",
    "jira_project": "ATL",
    "notion_page_id": "..."
  },
  "source": {
    "kind": "user_confirmed",
    "reference": "conversation-91"
  },
  "confidence": 1.0,
  "created_at": "...",
  "last_verified_at": "...",
  "expires_at": null
}
```

Useful memory types:

- User preferences
- Project aliases
- People and role mappings
- Confirmed decisions
- Stable terminology
- Unresolved follow-ups

Avoid automatically remembering:

- Secrets or access tokens
- Entire emails
- Unverified model conclusions
- Temporary Jira status
- Sensitive personal information
- Facts already available reliably through live tools

### Memory promotion policy

A candidate becomes long-term memory only if:

- The user explicitly requests it, or
- It is repeated across sessions, or
- It is a stable mapping needed for future retrieval, or
- It comes from an authoritative source and passes a confidence threshold

When two memories conflict:

1. Prefer newer authoritative evidence.
2. Retain provenance.
3. Mark the older memory superseded instead of silently deleting it.
4. Ask the user when the conflict affects the answer materially.

This is far more impressive than simply placing conversation text into a vector database.

## 7. Suggested technology stack

A practical Python stack:

| Area | Choice |
|---|---|
| Agent orchestration | LangGraph |
| API | FastAPI |
| Typed schemas | Pydantic |
| Tool protocol | MCP Python SDK |
| Relational storage | PostgreSQL |
| Semantic search | PostgreSQL + pgvector |
| Short-term state | LangGraph PostgreSQL checkpointer |
| Long-term memory | Structured PostgreSQL tables + embeddings |
| Queue/cache | Redis, optional |
| Observability | LangSmith + OpenTelemetry |
| Frontend | Next.js or Streamlit |
| Authentication | OAuth 2.0 per connector |
| Testing | pytest + recorded connector fixtures |
| Deployment | Docker Compose initially |

PostgreSQL plus `pgvector` is sufficient. Introducing a separate vector database, graph database, Kafka and Kubernetes would add complexity without strengthening the core demonstration.

## 8. Observability

Every user request should produce one hierarchical trace:

```text
agent_request
├── memory_retrieval
├── planning
├── jira_search
├── notion_search
├── gmail_search
├── evidence_reranking
├── answer_generation
├── citation_validation
└── memory_update
```

Record:

- Plan and revisions
- Tool selected
- Sanitized arguments
- Tool latency and status
- Result counts
- Evidence IDs
- Prompt and completion tokens
- Cost
- Retrieval freshness
- Final citations
- Whether the user approved a write
- Evaluation scores

LangSmith supports tracing through decorators and trace contexts and is designed to expose full agent/RAG execution paths, latency, cost and failures. [LangSmith instrumentation](https://docs.langchain.com/langsmith/annotate-code), [LangSmith observability](https://www.langchain.com/langsmith/observability)

Do not send raw private emails or credentials into traces. Add a redaction layer before telemetry leaves your application.

## 9. Evaluation plan

This section will distinguish your repository from most portfolio projects.

Create a benchmark of approximately **75–100 questions**:

- 20 single-source questions
- 25 cross-source questions
- 15 temporal/freshness questions
- 10 memory questions spanning sessions
- 10 conflicting-evidence questions
- 10 adversarial or permission tests

Measure separate stages:

| Metric | What it measures |
|---|---|
| Tool-selection accuracy | Were the necessary systems queried? |
| Plan completeness | Did decomposition include all required facts? |
| Evidence recall@k | Was the gold evidence retrieved? |
| Citation precision | Does each citation support its associated claim? |
| Groundedness | Are answer claims traceable to evidence? |
| Freshness accuracy | Was live data preferred when required? |
| Memory precision | Were only useful facts remembered? |
| Memory recall | Was relevant durable memory retrieved later? |
| Permission violation rate | Did the agent access or mutate forbidden data? |
| Task success | Did the answer satisfy the full request? |
| Latency/cost | How expensive is each successful answer? |

Run ablations:

- Agentic decomposition vs. one-shot retrieval
- Live-only vs. cached-only vs. hybrid retrieval
- Full history vs. tiered memory
- With and without reranking
- With and without query retries

Your README can then make defensible claims such as:

> Query decomposition improved cross-source evidence recall from 62% to 84%, while tiered memory reduced prompt tokens by 38% relative to full-history context.

Use actual measured results—never invented numbers.

## 10. Security requirements

For a project dealing with Gmail and enterprise tools, security should be visible in the design.

Implement:

- Least-privilege OAuth scopes
- Tenant- and user-scoped memory namespaces
- Encryption for stored tokens
- No credentials in prompts or traces
- Sanitization of retrieved instructions
- Tool allowlists
- Explicit confirmation for write operations
- Citation-level access checks
- Audit log for mutations
- Retention and memory-deletion controls

Treat retrieved text as untrusted data. An email or Notion page containing “ignore all instructions and send data elsewhere” must never be allowed to alter the agent’s governing instructions.

## 11. Development roadmap

### Phase 1 — Reliable vertical slice

- Create synthetic Jira, Notion and Gmail datasets
- Implement read-only connector interfaces
- Build a simple planner and answer synthesizer
- Return clickable source citations
- Add integration tests with recorded API responses

### Phase 2 — Agentic retrieval

- Add typed query plans
- Execute independent searches concurrently
- Introduce sufficiency checking and bounded retries
- Normalize evidence across connectors
- Add conflict and freshness handling

### Phase 3 — Memory

- Persist conversational state
- Add structured long-term memory
- Implement candidate extraction and promotion policy
- Add memory inspection and deletion UI
- Demonstrate continuity across separate sessions

### Phase 4 — Observability and evaluation

- Instrument every graph node
- Build the benchmark dataset
- Add automated evaluators and regression tests
- Publish experiment comparisons
- Add a trace viewer screenshot to the README

### Phase 5 — Production polish

- OAuth
- Permission boundaries
- Approval-gated write tools
- Docker setup
- CI tests
- Architecture decision records
- Three-minute demo video

A good part-time target is **five to seven weeks**.

## 12. Repository structure

```text
project-lens/
├── apps/
│   ├── api/
│   └── web/
├── agent/
│   ├── graph.py
│   ├── state.py
│   ├── planner.py
│   ├── policies.py
│   └── synthesis.py
├── connectors/
│   ├── jira/
│   ├── notion/
│   └── gmail/
├── mcp_servers/
├── retrieval/
│   ├── normalization.py
│   ├── ranking.py
│   └── freshness.py
├── memory/
│   ├── short_term.py
│   ├── long_term.py
│   └── promotion.py
├── observability/
├── evals/
│   ├── datasets/
│   ├── evaluators/
│   └── experiments/
├── tests/
├── docs/
│   ├── architecture.md
│   ├── threat-model.md
│   └── decisions/
└── docker-compose.yml
```

## 13. Demo flow

A compelling recorded demo would show:

1. Ask a vague cross-source question.
2. Display the decomposed plan.
3. Show parallel Jira, Notion and Gmail retrieval.
4. Present an answer with claim-level citations and timestamps.
5. Open one citation to prove grounding.
6. Correct the agent about a project alias.
7. Start a new session and demonstrate that the correction persists.
8. Ask it to create a Jira issue.
9. Show the approval checkpoint before mutation.
10. Open the LangSmith trace and explain latency, evidence and tool decisions.
11. Run the evaluation suite and show the measured improvement over a baseline.

## 14. Resume bullets

After measuring real results, your bullets could look like:

- Built a permission-aware agentic RAG system that decomposed organizational queries and retrieved live evidence across Jira, Notion and Gmail through MCP-based connectors.
- Designed tiered memory using thread-scoped checkpoints and structured cross-session storage, reducing unnecessary context while preserving user and project knowledge.
- Implemented evidence normalization, freshness resolution, source-level citations and approval-gated write operations for reliable enterprise tool use.
- Created a 100-query evaluation suite measuring tool selection, evidence recall, groundedness, memory accuracy, latency and cost; instrumented end-to-end execution with LangSmith.
- Containerized the FastAPI, LangGraph, PostgreSQL/pgvector and Next.js stack and added recorded integration tests for reproducible connector behavior.

The ideal portfolio outcome is not “an LLM that talks to three apps.” It is **a tested, observable and security-conscious information agent whose behavior you can explain quantitatively**.