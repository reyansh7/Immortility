# Immortility System Architecture

Below is a visual flowchart of how data and execution flow through Immortility's core modules, from user input to LLM execution and filesystem edits.

> [!IMPORTANT]
> Parts of the diagram below are historical. The vector store is **TurboVec**, not
> ChromaDB, and the chat model is resolved through the model registry
> (`config/models.yaml`, currently the Qwythos-9B Q4 brain) rather than a hardcoded
> `qwen3:8b`. See [`IMMORTALITY_AUDIT.md`](IMMORTALITY_AUDIT.md),
> [`IMMORTALITY_VISION.md`](IMMORTALITY_VISION.md), and
> [`IMMORTALITY_PHASES.md`](IMMORTALITY_PHASES.md).
>
> Shipped now: FAST/AGENT/BACKGROUND strategies (`core/execution_mode.py`),
> execution kernel (`core/execution_kernel.py`), harness traces (`core/harness.py`),
> deterministic capabilities (`core/capabilities.py`), Phase 2B primitives
> (git / documents / Docker inspect / configured databases) that all share the
> single command execution engine in `tools/command_tool.py`, and Phase 3 slice 3.0
> (`core/coding_engine.py`: Planner→Coder→Executor→Debugger→Reviewer→Reflector
> over those kernels). ECC skills/hooks, fresh-context review, and multimodal
> VL/OCR are **not** shipped.

```mermaid
flowchart TD
    %% Styling
    classDef user fill:#3b82f6,stroke:#2563eb,stroke-width:2px,color:white;
    classDef main fill:#8b5cf6,stroke:#7c3aed,stroke-width:2px,color:white;
    classDef router fill:#ec4899,stroke:#db2777,stroke-width:2px,color:white;
    classDef engine fill:#10b981,stroke:#059669,stroke-width:2px,color:white;
    classDef data fill:#f59e0b,stroke:#d97706,stroke-width:2px,color:white;
    classDef action fill:#ef4444,stroke:#dc2626,stroke-width:2px,color:white;
    classDef llm fill:#64748b,stroke:#475569,stroke-width:2px,color:white;

    User([User Input]):::user --> CLI["CLI Interface (main.py)"]:::main
    CLI --> ProjectDiscovery["Project Auto-Discovery\n(core/project_extract.py)"]:::main
    
    ProjectDiscovery --> Router{"Router\n(core/router.py)"}:::router
    
    %% Routes
    Router -->|"CHAT"| ChatRoute["Chat Mode"]:::engine
    Router -->|"ACTION"| ActionRoute["Action Mode"]:::action
    Router -->|"PROJECT"| ProjectRoute["Project Mode"]:::engine
    Router -->|"TASK"| TaskRoute["Workflow Mode"]:::action
    
    %% Knowledge Engine context pulling
    ProjectRoute -.-> |"Extract Context"| KE["Knowledge Engine\n(knowledge/engine.py)"]:::data
    TaskRoute -.-> |"Extract Context"| KE
    
    KE --> Chroma[("ChromaDB\n(Vector RAG)")]:::data
    KE --> Graph[("GraphEngine\n(Code Relationships)")]:::data
    
    %% Execution
    ChatRoute --> Ollama[("Ollama (qwen3:8b)")]:::llm
    
    ActionRoute --> ActionEngine["Action Engine\n(core/action_engine.py)"]:::action
    ProjectRoute -->|"Generate Plan & Confirm"| ActionEngine
    
    TaskRoute --> WF["Workflow Engine\n(core/workflow_engine.py)"]:::action
    WF --> ActionEngine
    
    %% Tool Loop
    ActionEngine -->|"JSON Tool Request"| Ollama
    Ollama -->|"Tool Call Payload"| ActionEngine
    ActionEngine -->|"Execute Action"| Tools["Tool Registry\n(read, write, terminal)"]:::action
    Tools -->|"Tool Result"| ActionEngine
    
    Tools --> FileSystem[("Local Filesystem")]:::data
    
    %% Verification Gate & Fallback
    ActionEngine -->|"Calls DONE"| Verifier{"Verification Gate\n(Mechanical + Semantic)"}:::engine
    Verifier -->|"Fails (Self-Correct)"| ActionEngine
    Verifier -->|"Fails 2+ Times"| Fallback["Fallback to Stronger Model\n(e.g., 14b+)"]:::llm
    Fallback -.->|"Updates Model"| Ollama
    
    %% Final Response
    Verifier -->|"Passes"| Response([Output to User]):::user
    ChatRoute --> Response
```

> [!NOTE] 
> The **Action Engine** forms an autonomous loop with the local LLM. It repeatedly calls tools from the **Tool Registry** (like reading files or running commands). Before completing, it must pass a **Verification Gate** (mechanical checks + semantic review). If it fails repeatedly, it can automatically fallback to a stronger model to self-correct.

## Phase 2B tool ecosystem (shipped)

Git, Docker inspect, document parsers, and configured databases are **primitives** in the Tool Kernel. There is no Git Agent, PDF Agent, Docker Agent, or Database Agent.

```
Tool Kernel (git_status, extract_document, docker_ps, db_query, run_command, …)
        │
        ├─ git_* / docker_* / run_command ──► CommandTool.execute_command
        │                                      (the only subprocess engine:
        │                                       timeout, cwd, limits, cancel, trace)
        ├─ extract_document ──► PyMuPDF / python-docx / python-pptx / openpyxl / csv
        └─ db_* ──► named connections only (config/databases.yaml or env)
```

Git permission model:

- Read-only (`status`, `diff`, `log`, `show`, branch list, `remote`) — no confirmation.
- Mutating (`add`, `commit`, `checkout`/`switch`, `stash` push/pop, `fetch`, non-force `push`, mixed/soft `reset`) — user confirmation via `pending_action`.
- Destructive (`reset --hard`, force-push, branch delete, stash drop/clear, commit `--amend`) — confirmation **and** `confirm_destructive=true`. Never silent.

Repo boundary: cwd must be inside the Immortility repo, the active project, or `IMMORTILITY_GIT_ALLOWED_ROOTS`. `git -C` / `--git-dir` from the model are rejected.

Limitations: no MySQL/Redis tools; Docker write ops besides confirmed `stop`/`rm` are not registered; Postgres/Mongo need drivers + a configured URL; parsers must be installed (`pymupdf`, `python-docx`, `python-pptx`, `openpyxl`).

## Phase 3 slice 3.0 — coding loop (started, not complete)

Thin roles over the existing kernels. Not a new agent framework and not a second shell.

```
Planner (success criteria) → inspect (discover_tools + git_status)
        → Coder (execute_action / Tool Kernel, confirmations intact)
        → Reviewer (git_status / git_diff only)
        → Executor (CommandTool pytest / py_compile allowlist)
        → Reflector (finish / retry debug / stop)
```

Retry budget is `IMMORTILITY_MAX_RETRIES` (same as the decision engine). Destructive git is never issued by this loop. ECC skills, hooks, and fresh-context review are **not** in this slice.
