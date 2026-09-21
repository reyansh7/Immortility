# 05 — Memory Architecture

## Why Memory Is Not Just a Vector Database

Vector databases store embeddings and retrieve the most semantically similar chunks. This is useful for finding relevant code or documentation. It is not memory.

Real memory requires:
- **Structure** — typed records with schemas, not flat text chunks
- **Provenance** — where did this information come from, and how confident are we?
- **Temporal validity** — when was this true, is it still true?
- **Contradiction handling** — what happens when two facts conflict?
- **Selective forgetting** — not all information should be retained forever
- **Confidence scoring** — some memories are stronger than others
- **Sensitivity awareness** — some information must never be stored

Immortality has multiple memory stores. Each serves a different purpose. The goal is not to unify them into a single store but to create a unified **query interface** that retrieves from the right stores for any given context.

---

## Memory Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    MEMORY SYSTEM                                │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  UNIFIED QUERY INTERFACE                                │   │
│  │  memory/personal_context.py [NEW]                       │   │
│  │  knowledge/engine.py::get_personal_context()           │   │
│  └──────────────────────────────┬──────────────────────────┘   │
│                                 │                               │
│      ┌──────────────────────────┼──────────────────────────┐   │
│      │                          │                          │   │
│  ┌───▼──────────┐   ┌──────────▼──────────┐   ┌──────────▼─┐  │
│  │   WORKING     │   │   EPISODIC          │   │  SEMANTIC  │  │
│  │   MEMORY      │   │   MEMORY            │   │  MEMORY    │  │
│  │               │   │                     │   │            │  │
│  │ state.json    │   │ experience_memory   │   │ TurboVec   │  │
│  │ → tasks.db    │   │ outcome_memory      │   │ (.vector_  │  │
│  │ [NEW]         │   │ conversation_memory │   │  db/)      │  │
│  │               │   │ self_reflection     │   │            │  │
│  │ Current task  │   │                     │   │ Code chunks│  │
│  │ Current conv  │   │ Past interactions   │   │ Doc chunks │  │
│  │ Pending state │   │ Task outcomes       │   │ Learned    │  │
│  │               │   │ Lessons learned     │   │   notes    │  │
│  └───────────────┘   └─────────────────────┘   └────────────┘  │
│                                                                 │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────┐  │
│  │  PROCEDURAL      │   │  PROJECT          │   │ PREFERENCE │  │
│  │  MEMORY          │   │  MEMORY           │   │ MEMORY     │  │
│  │                  │   │                   │   │            │  │
│  │ knowledge_graph  │   │ project_memory    │   │ preference │  │
│  │   _db.py         │   │ knowledge_graph   │   │  _memory   │  │
│  │ procedural_mem   │   │   _db.py          │   │ user_      │  │
│  │   [NEW]          │   │ hierarchical_mem  │   │  profile   │  │
│  │                  │   │                   │   │            │  │
│  │ How to do tasks  │   │ Project facts,    │   │ Name, lang,│  │
│  │ Patterns, skills │   │ AST knowledge,    │   │ editor,    │  │
│  │ Learned wf steps │   │ Reflections       │   │ style prefs│  │
│  └──────────────────┘   └───────────────────┘   └────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  WORLD MODEL [NEW]                                       │   │
│  │  memory/world_model.py                                   │   │
│  │                                                          │   │
│  │  Nodes: User | Project | File | App | Website | Person   │   │
│  │         | Task | Goal | Document | Service               │   │
│  │  Edges: owns | works_on | uses | depends_on | created    │   │
│  │         | completed | knows | bookmarked                 │   │
│  │  State: active | inactive | last_seen | last_modified    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Memory Types — Detailed Specification

### Working Memory
**What it is:** The current computational context. What the system is doing right now.

**Current implementation:** `core/agent_state.py` — singleton backed by `state.json`
- conversation_history (last N turns)
- pending_action (awaiting user confirmation)
- current_task (active task metadata)
- active_project (currently open project)
- pending_coding_request (coding plan awaiting confirmation)
- permission_mode (SAFE/ASSISTED/AUTONOMOUS/DEVELOPER)

**Target implementation (Phase 1):** Replace `current_task` dict with a proper `Task` object from `tasks.db`.

**Retrieval:** Direct access. No search needed.

**Forgetting:** Conversation history is trimmed to `max_history=30` turns.

**Sensitivity:** Low. No secrets should be in working memory. `state.json` is gitignored.

---

### Episodic Memory
**What it is:** Records of things that happened. What the system did, what worked, what failed.

**Current implementation:**
- `memory/experience_memory.py` — records task outcomes with outcome label (success/failure) and details text. Backed by `experience.json`.
- `memory/outcome_memory.py` — structured outcome scoring with retrieval by similarity.
- `memory/experience_dataset.py` — exports experiences as JSONL for eventual fine-tuning.
- `core/self_reflection.py` — extracts structured lessons after each task and stores them in:
  - ExperienceMemory (outcome record)
  - KnowledgeGraphDB (structured reflection)
  - TurboVec docs store (semantic retrieval)

**Retrieval:** `ExperienceMemory.get_relevant_experiences(query, limit=3)` — semantic similarity on task text.

**Target:** Add the full Task record (with per-step observations, tool call sequences) as the primary episodic record. Task records in `tasks.db` become the canonical episodic memory.

**Forgetting:** Experiences are never deleted automatically. Future: confidence decay for old, contradicted lessons.

**Sensitivity:** May contain file paths, project names. No secrets. Gitignored.

---

### Semantic Memory
**What it is:** Facts, concepts, and encoded knowledge. Not episodes, but generalizations.

**Current implementation:**
- TurboVec vector store (`.vector_db/turbovec/`) — hybrid BM25 + dense retrieval
  - `code_chunks` collection: 14k+ AST-chunked code snippets from indexed projects
  - `documentation` collection: imported documentation files
- `knowledge/knowledge_graph_db.py` — SQLite:
  - `repo_summaries` — per-project architecture summaries
  - `folder_summaries` — per-folder purpose summaries
  - `facts` — key-value facts per project (framework, language, patterns)
  - `reflections` — structured what-broke/what-fixed-it records
- `knowledge/hierarchical_memory.py` — builds folder/repo summaries from AST analysis

**Retrieval:** `knowledge/engine.py::get_context(query)` — hybrid search + graph expansion + context builder.

**What semantic memory is NOT:** It does not store the user's identity, preferences, or personal history. Those are in project memory, preference memory, and episodic memory.

**Forgetting:** Code chunks are invalidated when files change (watchdog). Semantic memory does not auto-delete. BGE-M3 migration (later phase) will require a full reindex.

---

### Procedural Memory
**What it is:** Patterns for how to perform tasks. "When fixing test failures in Python projects, always start with git_status + read the test file."

**Current implementation:** Partial. Self-reflection extracts lessons and stores them in both ExperienceMemory and TurboVec docs. There is no explicit "procedure" type.

**Target (Phase 8):**
- `memory/procedural_memory.py` — identifies repeated patterns across experience records
- Procedures are stored as structured objects: `{trigger, steps, tools, conditions, confidence}`
- Retrieved by task-type similarity and injected into agent context

**Forgetting:** Low-confidence procedures (based on few observations) decay. Procedures that repeatedly fail are demoted.

---

### Project Memory
**What it is:** Everything the system knows about a specific software project.

**Current implementation:**
- `memory/project_memory.py` — project metadata: name, path, language, framework, last opened, notes
- `knowledge/knowledge_graph_db.py` — per-project facts and reflections
- `knowledge/hierarchical_memory.py` — AST-derived: file symbols, folder summaries, repo summary
- TurboVec code_chunks — indexed source code
- `graphify-out/` — graphify code relationship graph (optional)

**Retrieval:** `knowledge/engine.py::get_context(query)` — assembles from all project stores.

**Forgetting:** Project memory is not auto-deleted. A future `/forget-project` command could remove it.

---

### Preference Memory
**What it is:** The user's stated and inferred preferences.

**Current implementation:**
- `memory/preference_memory.py` — key-value preferences (set_preference, get_preference)
- `memory/user_profile.py` — user name and profile facts
- `memory/memory_manager.py::auto_learn()` — extracts preferences from conversation and stores them

**Examples:** user_name, backend_language, preferred_editor, coding_style, verbosity_preference

**Sensitivity:** Low. No secrets. Names and preferences are appropriate to store.

**Target:** Preferences are injected into every system prompt, not just retrieved on demand.

---

### World Model
**What it is:** A living graph of the user's digital environment — what exists, how things relate, and their current state.

**Current implementation:** Partial. `core/desktop_scanner.py` provides live filesystem scans. `memory/project_memory.py` stores project metadata. There is no unified relationship graph.

**Target (Phase 5):**
```python
# Nodes
class WorldNode:
    id: str
    type: NodeType  # USER | PROJECT | FILE | APP | WEBSITE | PERSON | TASK | GOAL
    name: str
    properties: dict
    state: NodeState  # ACTIVE | INACTIVE | UNKNOWN
    last_seen: datetime
    confidence: float

# Edges
class WorldEdge:
    source_id: str
    target_id: str
    relation: EdgeRelation  # OWNS | WORKS_ON | USES | DEPENDS_ON | CREATED | COMPLETED
    weight: float
    created_at: datetime
```

**Why this differs from RAG:**
- RAG answers "which code chunk is most similar to this query?"
- The world model answers "what projects does the user own?", "what application is currently in focus?", "which task is currently running?", "what changed since yesterday?"

**Storage:** Extend `knowledge_graph.db` with world model tables. SQLite is sufficient.

---

## Memory Lifecycle Rules

### Storage
- Working memory: always written
- Episodic: written after every completed task
- Semantic: written when files are indexed or documents are imported
- Procedural: written after pattern detection (Phase 8)
- Project: written when a project is opened or modified
- Preference: written when auto-learn extracts a preference
- World model: updated continuously during task execution

### Retrieval
- Every agent call receives a personal context block assembled from all memory types
- The context builder prioritizes: working memory → project memory → relevant episodes → semantic hits → preferences
- Context is trimmed to fit within the model's context window

### Consolidation
- Experience records are summarized after a threshold (future: nightly consolidation job)
- Redundant semantic chunks are merged during reindex
- World model graph is normalized periodically

### Forgetting
- Conversation history: trimmed to N turns (configurable)
- Experience records: never auto-deleted, but confidence decays for old contradicted lessons
- Semantic chunks: invalidated when source files change
- World model nodes: marked INACTIVE when not seen for a threshold period

### Contradiction Handling
- When a new fact conflicts with a stored fact, both are retained with timestamps
- The newer fact is preferred in retrieval, but the old fact is preserved for audit
- Conflicting preferences are surfaced to the user for resolution

### Sensitivity
- Secrets (API keys, passwords) are filtered from all memory stores by `rag/security_filters.py`
- This filter runs before any chunk is written to TurboVec
- User-provided PII (name, email) is stored only in preference memory, not in semantic memory

---

## What Memory Powers Each Experience

| User Experience | Memory Types Used |
|---|---|
| "You know my name" | Preference memory |
| "You remember we were debugging X" | Episodic memory + working memory |
| "You know how my codebase is structured" | Semantic memory + project memory |
| "You know the right way to run my tests" | Procedural memory |
| "You know what projects I have" | World model + project memory |
| "You remember you fixed this type of bug before" | Episodic memory + procedural memory |
| "You know I prefer Python" | Preference memory |
| "You can pick up where we left off" | Working memory (task) + episodic |
