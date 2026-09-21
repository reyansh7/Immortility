# 19 — Architecture Decision Records

Architecture decisions are recorded here permanently. Once made, a decision is not reversed without an explicit new ADR.

Format: Status | Context | Decision | Rationale | Consequences

---

## ADR-001 — Immortality Owns the Harness

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
Multiple frameworks exist for building agent harnesses (LangGraph, CrewAI, AutoGen, Hermes). The question is whether Immortality should be built on one of these frameworks or own its harness natively.

**Decision:**
Immortality owns the harness. The execution loop, permission layer, tool dispatch, verification gate, and task lifecycle are first-party code in `core/`.

**Rationale:**
- The harness defines what Immortality IS. Delegating it to a framework makes Immortality a thin wrapper around someone else's product.
- The existing `action_engine.py` + `execution_kernel.py` + `coding_engine.py` already implement a working harness.
- Frameworks impose their own concepts (nodes, edges, crews, messages) that don't map cleanly to our architecture.
- A custom harness can be changed to meet our needs. A framework dependency means accepting the framework's design decisions.

**Consequences:**
- We maintain the harness. When we find bugs, we fix them ourselves.
- External agents (Hermes) are optional backends implementing the `AgentBackend` Protocol, not the identity of the system.
- Framework updates don't break our code.

---

## ADR-002 — Kimi K3 Is the Initial Primary Brain

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
The repository supports multiple model providers. A primary brain must be chosen as the default for reasoning and agent tasks.

**Decision:**
Kimi K3 via NVIDIA NIM (`moonshotai/kimi-k3`) is the primary brain. This is configured in `config/models.yaml`.

**Rationale:**
- 128K context window vs 8K for local 9B models
- State-of-the-art reasoning and coding capability
- OpenAI-compatible API — existing client code handles it with zero changes
- Zero VRAM cost (cloud API) — doesn't compete with embeddings or ASR on 8GB
- Already configured in the repository, just needs `NVIDIA_API_KEY`

**Consequences:**
- Requires NVIDIA API key and internet connectivity
- API costs per token
- User data goes to NVIDIA's servers for non-private tasks
- Local Ollama remains as fallback for offline/private tasks

---

## ADR-003 — Hermes Remains Replaceable and Optional

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
The repository has a complete `HermesBackend` adapter that calls an external Hermes gateway. The question is whether Hermes should be the required execution backend or an optional one.

**Decision:**
Hermes is an optional backend. The `AgentBackend` Protocol defines the interface. `HermesBackend` and `LegacyLoopBackend` (the existing tool loop) are both implementations. The default is `legacy`. Hermes activates only when `HERMES_API_KEY` is set and the gateway is running.

**Rationale:**
- The Hermes gateway server doesn't exist in this repository. It's an external dependency.
- Defaulting to "hermes" with an empty API key causes every request to silently fall through. This is confusing and incorrect.
- The legacy tool loop is a complete, working execution backend. It should be explicitly first-class.
- If Hermes becomes available as a local server, it can be activated via config without code changes.

**Consequences:**
- `core/config.py` default changes from `"hermes"` to `"legacy"`
- The `HermesBackend` adapter is preserved for future use
- Users who have Hermes running can activate it via `IMMORTILITY_AGENT_BACKEND=hermes`

---

## ADR-004 — No Premature Microservices or Distributed Infrastructure

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
Multiple distributed infrastructure options were evaluated for Immortality's backend: Redis, Celery, Temporal, NATS, Kafka.

**Decision:**
No distributed infrastructure. All coordination is in-process. Event monitoring uses asyncio. Background tasks use ThreadPoolExecutor. Persistence uses SQLite.

**Rationale:**
- Immortality runs on one personal machine for one user
- Event volumes are: git polls (1/5min), schedule checks (1/min), file events (tens/hour)
- asyncio.Queue handles these volumes trivially
- Adding Redis requires a server process to be always running
- If the Redis server crashes, Immortality loses event history
- Temporal is for distributed microservice workflows, not personal AI on a laptop

**Consequences:**
- All state lives in SQLite files on the local machine
- No external server dependencies beyond Ollama and NVIDIA NIM
- The system starts faster (no waiting for Redis/Temporal)
- Event processing is bounded by single-machine capacity (sufficient for personal AI)

---

## ADR-005 — Memory Is Multi-Layered, Not Just Vector Search

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
Many AI systems conflate "memory" with "vector database". Immortality has multiple memory stores serving different purposes.

**Decision:**
Memory is explicitly layered:
- Working memory: `state.json` → `tasks.db`
- Episodic memory: `experience_memory.py` + `outcomes.db`
- Semantic memory: TurboVec vector store
- Procedural memory: knowledge_graph reflections (Phase 8 expansion)
- Project memory: `project_memory.py` + `knowledge_graph.db`
- Preference memory: `preference_memory.py`
- World model: `world_model.py` + `knowledge_graph.db` world tables

**Rationale:**
- Vector search answers "which chunk is most similar?". It cannot answer "what happened yesterday?" or "what projects do I have?"
- Different memory types need different storage, retrieval, and forgetting strategies
- Unifying everything into a vector database loses the structure that enables personal intelligence

**Consequences:**
- More code to maintain (multiple subsystems)
- Richer capabilities (can answer temporal and structural questions)
- The unified query interface (`personal_context.py`) hides complexity from the agent

---

## ADR-006 — The Permission Layer Is Below the LLM

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
The LLM generates tool calls. The question is whether the LLM should be trusted to apply its own judgment about what's safe, or whether a separate policy layer should enforce permissions.

**Decision:**
The permission layer is a separate, synchronous module (`core/permissions.py`) that runs before every tool execution. The LLM cannot bypass it regardless of what instructions it receives.

**Rationale:**
- LLMs can be manipulated via prompt injection
- LLMs don't have reliable, consistent judgment about what is destructive
- A deterministic policy is auditable and testable
- This is a fundamental security property of the system

**Consequences:**
- The LLM never executes a tool directly; it proposes tool calls
- Destructive actions always require user confirmation regardless of mode
- The permission system can be tested without any LLM calls

---

## ADR-007 — Computer Control Uses Structured Interfaces Before Vision

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
Computer control can be implemented via (a) accessibility APIs, (b) vision-based targeting, or (c) coordinate-based mouse simulation.

**Decision:**
The control hierarchy from preferred to fallback:
1. Programmatic APIs (git, filesystem, browser DOM, CLIs)
2. Browser accessibility tree (Playwright)
3. Windows UI Automation (pywinauto)
4. Vision-based targeting (vision model → coordinates)
5. Coordinate-based simulation (last resort)

**Rationale:**
- Accessibility APIs are reliable, resolution-independent, and don't require the screen to be visible
- Vision-based targeting is slow (~10-30s per call) and depends on model quality
- Coordinate-based simulation breaks across DPI settings and window positions

**Consequences:**
- pywinauto becomes the primary computer control library for native Windows apps
- Vision model is required only when UIA fails (limited accessibility exposure)
- Mouse/keyboard coordinates are never computed by the LLM

---

## ADR-008 — Tasks Are Durable

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
Currently, tasks die with the process. There is no unified Task object with persistent state.

**Decision:**
Every meaningful multi-step operation is a `Task` object persisted in `tasks.db`. Tasks survive process restarts. On startup, interrupted tasks are offered for resumption.

**Rationale:**
- A JARVIS-level system must be able to continue work after an interruption
- Process crashes and user-initiated restarts should not lose task context
- The task record provides the audit trail for what was done and why

**Consequences:**
- Phase 1 creates `core/task.py` + `core/task_db.py` + `core/task_manager.py`
- `action_engine.py` must create and update Task records
- Startup recovery code handles interrupted tasks

---

## ADR-009 — Existing Working Systems Are Preserved

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
The codebase has many working subsystems. There is a temptation to rewrite them for architectural elegance.

**Decision:**
Working systems are preserved. Refactoring is additive (adding interfaces, adding methods). Rewrites are only justified by a specific failing behavior that cannot be fixed incrementally.

**Rationale:**
- Rewrites introduce bugs. Preservation maintains stability.
- The test suite validates existing behavior. A rewrite must be followed by re-passing the full test suite.
- Architectural elegance is a lower priority than working capability.

**Consequences:**
- `workflow_engine.py`, `action_engine.py`, `llm.py`, `tool_registry.py` are modified, not rewritten
- New capabilities are added as new files, not by refactoring existing ones
- Technical debt in existing files is addressed incrementally

---

## ADR-010 — SQLite Over PostgreSQL for Personal AI

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
PostgreSQL is a popular choice for AI systems. The question is whether Immortality needs PostgreSQL.

**Decision:**
SQLite for all persistent storage (workflow.db, knowledge_graph.db, outcomes.db, tasks.db).

**Rationale:**
- Immortality is single-user, single-process (mostly)
- Data volumes: hundreds to thousands of rows, not millions
- SQLite requires no server process — zero operational overhead
- SQLite is faster than PostgreSQL for read-heavy workloads at this scale
- PostgreSQL adds ~50MB server overhead and requires daemon management

**Consequences:**
- No concurrent multi-process writes (acceptable — single user)
- No full-text search (acceptable — TurboVec handles semantic search)
- No pgvector (acceptable — TurboVec handles vector search)
- If requirements change (multi-user, multi-process), revisit

---

## ADR-011 — TurboVec Over ChromaDB

**Status:** ACCEPTED (already executed)  
**Date:** Prior to this audit

**Context:**
The project migrated from ChromaDB to TurboVec for the vector store.

**Decision:**
TurboVec is the permanent vector store. The migration is complete. No rollback.

**Rationale:**
- TurboVec + BM25 provides hybrid search (semantic + keyword) that ChromaDB did not
- The migration is already complete and ~14k chunks are indexed
- Re-migrating would require a full reindex with no capability gain

**Consequences:**
- ChromaDB is no longer a dependency
- `immortility_architecture.md` references to ChromaDB are stale (the stale doc is noted in the audit)
- BGE-M3 migration (Phase 6) will require another full reindex

---

## ADR-012 — Voice Routes to Full Agent Loop

**Status:** ACCEPTED (pending implementation)  
**Date:** 2026-09-21

**Context:**
Voice currently routes to `fast_chat()` only. "JARVIS, run the tests" returns a spoken description instead of running the tests.

**Decision:**
Voice input routes through the full `classify_route()` + strategy + agent dispatch path, identical to typed input. The only exception is the fast path for short conversational turns.

**Rationale:**
- The defining JARVIS interaction is "speak → it acts". This cannot work with fast_chat only.
- The full agent path already exists and handles voice input correctly
- The change required is a single function modification in `main.py`

**Consequences:**
- Voice tasks take longer (agent loop vs fast_chat response time)
- Spoken acknowledgment is needed before long tasks ("On it.")
- Voice can now trigger any capability the typed interface can trigger

---

## ADR-013 — The Vision Model Must Be Local

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
Screenshots may contain sensitive information. Should the vision model be cloud or local?

**Decision:**
The vision model must be local (Ollama). Screenshots are never sent to cloud APIs. Cloud vision (GPT-4V, Gemini Vision) is not used even when available.

**Rationale:**
- Screenshots may contain personal files, passwords, private messages
- Local-first is a core principle of Immortality
- Qwen2.5-VL-7B fits on 8GB VRAM (with brain eviction) and is adequate for screen understanding

**Consequences:**
- Vision is slower than cloud alternatives
- Vision quality is bounded by 7B-class models
- Users must explicitly pull and configure the vision model

---

## ADR-014 — MCP Is Deferred

**Status:** ACCEPTED  
**Date:** 2026-09-21

**Context:**
Model Context Protocol (MCP) is becoming an industry standard for tool interfaces. Should Immortality adopt it now?

**Decision:**
MCP is deferred to Phase 6+. All current tools remain as internal Python functions in `tools/tool_registry.py`.

**Rationale:**
- All current tools run in-process — MCP would add subprocess management overhead for no gain
- The internal tool registry already provides discovery, schema, and permission metadata
- MCP becomes useful when integrating external services (calendar, email, Notion) — none of which are current priorities

**Consequences:**
- Tool interface remains internal for now
- When external service integration is needed (Phase 6+), MCP servers wrap those services
- The internal registry continues to grow as needed
