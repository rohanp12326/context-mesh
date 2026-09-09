# ADR 001: Architecture Decision Record - Model Context Protocol & Tiered Memory

## Context
Cross-tool enterprise AI agents frequently suffer from fragile connector coupling, state pollution, stale retrieved context, and unpredictable tool calling loops.

## Decisions

### 1. Adopt Model Context Protocol (MCP)
- **Decision**: Wrap Jira, Notion, and Gmail integrations behind standard MCP tool definitions.
- **Rationale**: Decouples the planner from specific API client nuances. Connectors can be replaced, upgraded, or mocked without altering the agent planning and synthesis graphs.

### 2. Tiered Memory (Short-Term Checkpointer + Long-Term Structured Store)
- **Decision**: Separate thread-scoped working state (messages, rolling plan) from cross-session durable memory (project aliases, verified roles).
- **Rationale**: Placing full conversation history or transient statuses into vector databases creates semantic drift and token bloat. Structured long-term memory with promotion rules ensures only stable, verified facts persist.

### 3. Dual-Mode Execution (Mock Synthetic Fixtures + Live REST APIs)
- **Decision**: Provide comprehensive synthetic datasets for "Project Atlas" while supporting live enterprise credentials.
- **Rationale**: Allows 100% reproducible, offline unit tests, evaluation benchmarks, and continuous integration without requiring live enterprise API keys.
