# 01 — System Architecture

## Overview

Immortality is a **modular monolith**. There are no microservices, no message brokers, no orchestration platforms. The entire system runs as a single Python process on the user's Windows machine, coordinated through in-process modules.

This is intentional. A personal AI runtime running on a laptop does not need distributed infrastructure. The architectural boundaries are module-level, not service-level.

---

## Current Runtime Architecture (as traced from code)

```
┌─────────────────────────────────────────────────────────┐
│                    IMMORTALITY PROCESS                  │
│                                                         │
│  ┌─────────────────────┐  ┌──────────────────────────┐ │
│  │   CLI (main.py)     │  │  HUD (http :8765)        │ │
│  │   Rich terminal     │  │  immortility_hud.html    │ │
│  │   prompt_toolkit    │  │  stdlib http.server      │ │
│  └──────────┬──────────┘  └───────────┬──────────────┘ │
│             └──────────────┬───────────┘                │
│                            │                            │
│             ┌──────────────▼───────────────┐            │
│             │      INTENT LAYER             │            │
│             │  core/router.py               │            │
│             │  classify_route()             │            │
│             │  CHAT | ACTION | PROJECT |    │            │
│             │  TASK | RESEARCH_TASK         │            │
│             │                               │            │
│             │  core/execution_mode.py       │            │
│             │  FAST | AGENT | BACKGROUND    │            │
│             └──────────────┬───────────────┘            │
│                            │                            │
│      ┌─────────────────────┼──────────────────┐         │
│      │                     │                  │         │
│ ┌────▼──────┐   ┌───────────▼────────┐  ┌─────▼──────┐  │
│ │  FAST     │   │  AGENT / PROJECT   │  │ BACKGROUND │  │
│ │  PATH     │   │                    │  │            │  │
│ │           │   │ core/execution_    │  │ ExecutionK │  │
│ │ core/     │   │ kernel.py          │  │ ernel.run_ │  │
│ │ llm.py    │   │ ExecutionKernel    │  │ task()     │  │
│ │ fast_chat │   │                    │  │            │  │
│ └────┬──────┘   └───────────┬────────┘  └────────────┘  │
│      │                      │                           │
│      │          ┌───────────▼────────────────────────┐  │
│      │          │  AGENT BACKEND DISPATCH             │  │
│      │          │  core/action_engine.py              │  │
│      │          │  execute_action()                   │  │
│      │          │                                     │  │
│      │          │  ┌───────────────┐ ┌─────────────┐ │  │
│      │          │  │ HermesBackend │ │ LegacyLoop  │ │  │
│      │          │  │ (when API key │ │ (default)   │ │  │
│      │          │  │  is set)      │ │             │ │  │
│      │          │  └───────────────┘ └──────┬──────┘ │  │
│      │          └──────────────────────────┬─┘        │  │
│      │                                     │          │  │
│      │          ┌──────────────────────────▼───────┐  │  │
│      │          │  PERMISSION LAYER                 │  │  │
│      │          │  core/permissions.py              │  │  │
│      │          │  SAFE | ASSISTED | AUTONOMOUS     │  │  │
│      │          │  | DEVELOPER                      │  │  │
│      │          └──────────────────────────┬────────┘  │  │
│      │                                     │           │  │
│      │          ┌──────────────────────────▼────────┐  │  │
│      │          │  TOOL RUNTIME                      │  │  │
│      │          │  tools/tool_registry.py            │  │  │
│      │          │  ~40 tools registered              │  │  │
│      │          │                                    │  │  │
│      │          │  filesystem  git    docker         │  │  │
│      │          │  terminal    web    browser        │  │  │
│      │          │  documents   db     system         │  │  │
│      │          └──────────────────────────┬─────────┘  │  │
│      │                                     │            │  │
│      └──────────────────────┐              │            │  │
│                             │              │            │  │
│      ┌──────────────────────▼──────────────▼──────────┐ │  │
│      │              MODEL LAYER                        │ │  │
│      │  core/llm.py  models/registry  models/router   │ │  │
│      │  models/manager  models/vram  models/doctor     │ │  │
│      │                                                 │ │  │
│      │  Providers: nvidia | ollama | vllm | gemini     │ │  │
│      │  Roles: brain | code | vision | embed | asr | tts│ │  │
│      └────────────────────────────────────────────────┘ │  │
│                                                          │  │
│      ┌───────────────────────────────────────────────┐   │  │
│      │              MEMORY LAYER                     │   │  │
│      │                                               │   │  │
│      │  memory/   knowledge/   rag/                  │   │  │
│      │                                               │   │  │
│      │  AgentState (state.json)  ← working           │   │  │
│      │  ConversationMemory       ← episodic          │   │  │
│      │  ProjectMemory            ← project           │   │  │
│      │  ExperienceMemory         ← procedural        │   │  │
│      │  PreferenceMemory         ← preferences       │   │  │
│      │  KnowledgeGraphDB         ← facts/reflections │   │  │
│      │  TurboVec (.vector_db/)   ← semantic          │   │  │
│      └───────────────────────────────────────────────┘   │  │
│                                                           │  │
│      ┌────────────────────────────────────────────────┐   │  │
│      │           OBSERVABILITY                        │   │  │
│      │  core/harness.py  TraceEvent                   │   │  │
│      │  logs/immortility_events.jsonl                 │   │  │
│      │  tools/hud_vitals.py (CPU/RAM/VRAM)            │   │  │
│      └────────────────────────────────────────────────┘   │  │
│                                                            │  │
└────────────────────────────────────────────────────────────┘
```

---

## Target Architecture (Immortality as JARVIS Runtime)

This is the architecture we are building toward. Components marked `[NEW]` do not yet exist. Components marked `[EXPAND]` exist but need significant extension.

```
┌────────────────────────────────────────────────────────────────┐
│                      IMMORTALITY                               │
│                    JARVIS RUNTIME                              │
└──────────────────────────┬─────────────────────────────────────┘
                           │
          ┌────────────────▼────────────────┐
          │         INTENT LAYER            │
          │  Voice wakeword [NEW]           │
          │  STT → text (existing)          │
          │  Intent classifier (existing)   │
          │  Fast-path detection (existing) │
          └────────────────┬────────────────┘
                           │
          ┌────────────────▼────────────────┐
          │        TASK RUNTIME [NEW]       │
          │  Task creation + persistence    │
          │  tasks.db (SQLite)              │
          │  goal / plan / steps / state    │
          │  checkpoint / rollback          │
          │  resume on restart              │
          └──────┬─────────────────┬────────┘
                 │                 │
    ┌────────────▼─┐           ┌───▼──────────────┐
    │  PLANNER     │           │  MEMORY LAYER    │
    │  LLM-based   │           │  (all existing + │
    │  or heuristic│           │   world model    │
    │  task graph  │           │   [EXPAND])      │
    └────────────┬─┘           └───────────────────┘
                 │
    ┌────────────▼────────────────────────────────┐
    │          EXECUTION KERNEL (existing)        │
    │  cancel / timeout / retry / trace / cache  │
    └────────────┬────────────────────────────────┘
                 │
    ┌────────────▼────────────────────────────────┐
    │            MODEL ROUTER (existing)          │
    │  brain | code | vision | embed | asr | tts  │
    │  VRAM-aware, health-checked, fallback       │
    └────────────┬────────────────────────────────┘
                 │
    ┌────────────▼────────────────────────────────┐
    │         PERMISSION LAYER (existing)         │
    │  SAFE / ASSISTED / AUTONOMOUS / DEVELOPER   │
    │  LLM cannot bypass this layer               │
    └────────────┬────────────────────────────────┘
                 │
    ┌────────────▼────────────────────────────────────────────┐
    │                 TOOL RUNTIME                            │
    │                                                         │
    │  ┌─────────────┐ ┌──────────────┐ ┌───────────────┐    │
    │  │ Code Tools  │ │ System Tools │ │ Browser Tools │    │
    │  │ (existing)  │ │ (existing)   │ │ (existing)    │    │
    │  └─────────────┘ └──────────────┘ └───────────────┘    │
    │  ┌─────────────┐ ┌──────────────┐                      │
    │  │ Computer    │ │ Vision Tools │                      │
    │  │ Control     │ │ [NEW]        │                      │
    │  │ [NEW]       │ │              │                      │
    │  └─────────────┘ └──────────────┘                      │
    └────────────────────────────────────┬────────────────────┘
                                         │
    ┌────────────────────────────────────▼────────────────────┐
    │              OBSERVATION LOOP                           │
    │  screenshot() → vision model → world-state update      │
    │  git_status() → project state                          │
    │  test_run() → verification                             │
    │  tool_result() → task observation                      │
    └────────────────────────────────────┬────────────────────┘
                                         │
    ┌────────────────────────────────────▼────────────────────┐
    │              VERIFICATION + REFLECTION                  │
    │  diff review (existing) + CI gate (existing)           │
    │  self_reflection.py (existing)                         │
    │  experience recording (existing)                       │
    └─────────────────────────────────────────────────────────┘
```

---

## Module Map (Current Repository)

```
immortility1/
│
├── main.py                    Entry point — CLI + voice + HUD launcher
│
├── core/                      Core runtime
│   ├── action_engine.py       Primary agent loop (LLM → permission → tool → verify)
│   ├── agent_backend.py       AgentBackend Protocol (interface abstraction)
│   ├── agent_state.py         Process-wide state singleton (state.json)
│   ├── capabilities.py        Deterministic capability registry
│   ├── chat_thread.py         Conversation thread management
│   ├── checkpoint_manager.py  File checkpoint/rollback for workflow recovery
│   ├── coding_engine.py       Autonomous coding loop (P→C→R→E→V→Ref)
│   ├── coding_planner.py      LLM-based coding plan generator
│   ├── coding_reviewer.py     Fresh-context code reviewer
│   ├── config.py              Central configuration (env-driven)
│   ├── control_plane.py       /mode /resume /handoff commands
│   ├── critic.py              Output quality assessment
│   ├── decision_engine.py     Workflow step transition logic
│   ├── desktop_scanner.py     Live filesystem scan (projects/desktop)
│   ├── event_bus.py           In-process pub/sub event bus
│   ├── execution_kernel.py    Capability-agnostic kernel (cancel/timeout/trace)
│   ├── execution_mode.py      FAST/AGENT/BACKGROUND classifier
│   ├── framework_hints.py     Project framework detection
│   ├── harness.py             Trace event recording (TTFT, tool timing, etc.)
│   ├── hermes_backend.py      Hermes gateway HTTP adapter
│   ├── hooks.py               Lifecycle hooks (BEFORE_PLAN, AFTER_REVIEW, etc.)
│   ├── intent.py              Intent classification helpers
│   ├── json_utils.py          LLM JSON output parser/fixer
│   ├── llm.py                 Multi-provider LLM client
│   ├── paths.py               Path resolution utilities
│   ├── pending_action.py      Pending confirmation management
│   ├── permissions.py         Permission mode enforcement
│   ├── project_extract.py     Project path extraction from user input
│   ├── project_resolve.py     Project name resolution
│   ├── project_runner.py      Dev server launcher
│   ├── reply_format.py        Output polish and formatting
│   ├── repo_paths.py          Canonical path constants
│   ├── research_context.py    Research context container
│   ├── router.py              Intent router (CHAT/ACTION/PROJECT/TASK)
│   ├── self_reflection.py     Post-task lesson extraction and storage
│   ├── source_router.py       Source selection (RAG vs graph vs learned)
│   ├── text_sanitize.py       Input sanitization
│   ├── tool_cache.py          Read-only tool result caching
│   ├── verifier.py            Project verification (syntax + build + tests)
│   ├── workflow_engine.py     Phase 3 workflow orchestrator (state machine)
│   ├── workflow_executor.py   Workflow step execution
│   ├── workflow_history.py    Workflow event log
│   ├── workflow_scheduler.py  Pause/resume coordination
│   ├── workflow_state.py      SQLite-backed workflow state (workflow.db)
│   └── workflow_validator.py  Input validation for workflow context
│
├── models/                    Model layer
│   ├── __init__.py
│   ├── doctor.py              Fleet health report (--doctor)
│   ├── manager.py             VRAM admission control + eviction
│   ├── registry.py            Config parser (config/models.yaml)
│   ├── router.py              Capability-based model selector
│   ├── types.py               ModelSpec, ModelHealth, Selection types
│   └── vram.py                nvidia-smi GPU/RAM probing
│
├── memory/                    Memory subsystems
│   ├── conversation_memory.py  Current conversation history
│   ├── experience_dataset.py   JSONL experience dataset builder
│   ├── experience_memory.py    Task outcome recording
│   ├── memory_manager.py       Unified memory interface + auto-learn
│   ├── memory.txt              (static memory notes)
│   ├── outcome_memory.py       Outcome scoring and retrieval
│   ├── preference_memory.py    User preferences
│   ├── project_memory.py       Project metadata
│   ├── response_tuner.py       Response quality tuning
│   ├── session_memory.py       Ephemeral session state
│   ├── session_resume.py       Session handoff capture/restore
│   └── user_profile.py         User identity and profile
│
├── knowledge/                 Knowledge and retrieval
│   ├── ast_extractor.py        AST-based code symbol extraction
│   ├── context_builder.py      RAG context assembly
│   ├── context_ranker.py       Retrieval result ranking
│   ├── engine.py               KnowledgeEngine singleton (primary interface)
│   ├── graph_engine.py         Graphify code graph wrapper
│   ├── hierarchical_memory.py  AST → folder → repo summary hierarchy
│   ├── knowledge_graph_db.py   SQLite facts/reflections store
│   ├── learner.py              TurboVec document ingestion
│   ├── project_manager.py      Project open/index/watch coordination
│   ├── reflection_format.py    Reflection block formatting
│   ├── retrieval_cache.py      Query result cache
│   └── smart_context_builder.py  Structured context layout builder
│
├── rag/                       Retrieval-augmented generation
│   ├── bulk_ingestor.py        Multi-project indexing
│   ├── chunker.py              AST-aware code chunker
│   ├── embeddings.py           Sentence-transformer embedding wrapper
│   ├── hybrid_search.py        BM25 + semantic fusion
│   ├── index_state_db.py       Incremental index state tracking
│   ├── indexer.py              TurboVec chunk indexer
│   ├── project_indexer.py      Per-project indexing pipeline
│   ├── reranker.py             CrossEncoder reranker (optional)
│   ├── retriever.py            Primary retrieval interface
│   ├── security_filters.py     Secret detection in chunks
│   └── vector_store.py         TurboVec wrapper
│
├── editing/                   Code editing safety and validation
│   ├── ast_editor.py           AST-safe code transformations
│   ├── ci_gate.py              CI allowlist and build verification
│   ├── coding_workflow.py      CodingWorkflow end-to-end pipeline
│   ├── diff_logger.py          Unified diff recording
│   ├── edit_planner.py         File-level edit plan generation
│   ├── error_classifier.py     Build/test error categorization
│   ├── file_editor.py          Safe file edit primitives
│   ├── patch_args.py           Patch argument normalization
│   ├── patch_generator.py      LLM-driven patch generation
│   ├── patch_validator.py      Patch syntax and safety validation
│   ├── reflection_engine.py    Post-edit reflection
│   ├── self_debug.py           Autonomous debug loop
│   ├── symbol_finder.py        Symbol location in codebase
│   ├── unified_diff.py         Diff format utilities
│   └── verifier.py             Post-edit file verification
│
├── agents/                    Named role helpers (not true agents)
│   ├── memory_agent.py         Memory extraction helper
│   ├── research_agent.py       Web research + synthesis
│   └── research_synth.py       Research result synthesizer
│
├── tools/                     Tool implementations
│   ├── app_tool.py             Application launcher
│   ├── browser_agent.py        Playwright browser automation (goal-based)
│   ├── browser_manager.py      Playwright browser lifecycle
│   ├── browser_tool.py         Low-level browser primitives
│   ├── command_tool.py         Shell command execution (sandboxed)
│   ├── database_tool.py        SQLite/Postgres/Mongo (named connections)
│   ├── desktop_fs.py           Desktop filesystem helpers
│   ├── docker_tool.py          Docker inspect primitives
│   ├── document_tool.py        PDF/DOCX/XLSX/PPTX/CSV extraction
│   ├── file_tool.py            Filesystem CRUD + code graph queries
│   ├── git_tool.py             Git operations (read-only + confirmed mutating)
│   ├── hud_agent.py            HUD agent message handler
│   ├── hud_knowledge.py        HUD knowledge management
│   ├── hud_launcher.py         HUD server process
│   ├── hud_notify.py           HUD notification system
│   ├── hud_state.py            HUD state synchronization
│   ├── hud_todos.py            HUD task/todo management
│   ├── hud_upload.py           HUD file upload handler
│   ├── hud_vitals.py           System health sampling (CPU/RAM/VRAM)
│   ├── leetcode_tool.py        LeetCode problem solver
│   ├── link_inspect.py         URL content fetcher
│   ├── multi_actions.py        Parallel tool execution
│   ├── scraper_tool.py         Web page scraping
│   ├── self_inspect.py         Self-inspection tools
│   ├── system_control.py       Brightness/volume control
│   ├── system_tool.py          Process list and system info
│   ├── tool_registry.py        Central tool registry (~40 tools)
│   ├── user_browser.py         User-facing browser interaction
│   ├── voice_io.py             Whisper STT + Windows SAPI TTS
│   ├── voice_vocab.py          Whisper vocabulary biasing
│   └── web_search_tool.py      Multi-provider web search
│
├── config/
│   ├── databases.yaml          Named database connection configs
│   └── models.yaml             Model fleet configuration
│
├── prompts/
│   ├── fast_system.txt         FAST path system prompt
│   ├── system.txt              Main agent system prompt
│   ├── tool_system.txt         Tool loop system prompt
│   └── rules/                  Durable rule files (loaded by skill match)
│
├── skills/                    On-demand skill loader
│   ├── leetcode_skill.py
│   └── registry.py
│
├── frontend/
│   └── immortility_hud.html    Single-page JARVIS HUD
│
├── scripts/
│   ├── ollama/                 Ollama Modelfile
│   └── wsl/                   vLLM startup scripts
│
└── tests/                     pytest test suite (~50 test files)
```

---

## Data Flow: Five Key Paths

### Path 1 — Fast Chat
```
User types "hi" or short question
→ router._fast_route() matches regex → CHAT
→ classify_strategy() → FAST
→ llm.fast_chat() → Ollama/Kimi K3
→ stream response to terminal/HUD
```

### Path 2 — Agent Task (e.g. "fix the failing tests")
```
User types request
→ router.classify_route() → ACTION
→ classify_strategy() → AGENT
→ execution_kernel.run_model() (begin turn trace)
→ [if HERMES_API_KEY set] hermes_backend.run() → Hermes gateway
→ [else] action_engine.execute_action()
    → permissions.decide() per tool
    → tool_registry.execute(tool_name, args)
    → verification gate on DONE
→ self_reflection.reflect_from_action_result()
→ response to terminal/HUD
```

### Path 3 — Project / Coding (e.g. "implement feature X")
```
User types coding request with open project
→ router.classify_route() → PROJECT
→ knowledge.engine.get_context() [hybrid BM25 + semantic retrieval]
→ editing.coding_workflow.CodingWorkflow.run()
    → coding_engine.run_coding_loop()
        → coding_planner.plan_coding_task()
        → action_engine.execute_action() [coder role]
        → coding_reviewer.review_coding_result()
        → executor: pytest + py_compile
        → self_debug if failing
        → reflect and record
→ response to terminal/HUD
```

### Path 4 — Voice Loop
```
User says something (after /talk)
→ voice_io.VoiceIO.listen() [Whisper STT, offline]
→ main._looks_like_fast_chat()
    → [yes] llm.fast_chat()
    → [no] full router + action path (currently: only fast_chat)
→ voice_io.VoiceIO.speak() [Windows SAPI TTS]
```

### Path 5 — Background Workflow
```
User requests long-running task
→ classify_strategy() → BACKGROUND
→ execution_kernel.run_task() [ThreadPoolExecutor]
→ workflow_engine.WorkflowEngine.run_workflow()
    → PLANNING → RESEARCH → KNOWLEDGE_RETRIEVAL →
      EDIT_PLANNER → PATCH_GENERATOR → VERIFIER →
      DEBUGGER → REFLECTOR → EXPERIENCE_UPDATE
→ checkpoint_manager saves files at key points
→ HUD progress updates via hud_state.update_hud()
```

---

## External Dependencies

| Dependency | Purpose | Required? |
|---|---|---|
| Ollama (local) | Local LLM inference | Preferred for local |
| NVIDIA NIM API | Kimi K3 cloud inference | Required for Kimi K3 |
| Hermes gateway | Optional agent execution backend | Optional (currently inactive) |
| Playwright browsers | Browser automation | For browser tools |
| faster-whisper | Offline STT | For voice |
| pyttsx3 / pywin32 | Offline TTS | For voice |
| TurboVec | Vector store | For RAG |
| sentence-transformers | Embeddings | For RAG |

---

## Architectural Invariants

These properties must be preserved through all future development:

1. The permission layer executes synchronously before every tool call. No async bypass.
2. The tool registry is the single point of tool invocation. No tool is called directly.
3. The model layer is the single point of LLM invocation. No direct API calls elsewhere.
4. The knowledge engine is the single point of retrieval. No direct vector store queries from agents.
5. `state.json` is not a database. Critical state (workflows, tasks) uses SQLite.
6. Destructive actions are always confirmed regardless of permission mode.
