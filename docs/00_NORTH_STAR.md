# 00 — NORTH STAR

## What Is Immortality?

**Immortality** is a persistent personal intelligence runtime.

It is not a chatbot. It is not a wrapper around a cloud API. It is not a collection of agents.

Immortality is a system that can perceive, remember, reason, plan, act, learn, and operate across a user's digital world — continuously, with increasing competence, under explicit permissions, and with verifiable results.

The system runs locally. The user's data, context, and identity stay on their machine. The intelligence is composable, not monolithic: a capable model coordinates with tools, memory, and observation loops — all owned by the Immortality runtime, not delegated to an external service.

---

## The JARVIS Concept

"JARVIS" is the personality, experience, and identity layer of Immortality — not a separate product, not a rename.

When the user interacts with Immortality, the experience should feel like JARVIS from Iron Man: a calm, persistent, capable intelligence that knows who the user is, remembers previous context, executes tasks rather than merely describing them, and communicates naturally in voice or text.

The underlying platform is **Immortality**. JARVIS is the north star for the user experience.

**Do not rename the project to JARVIS.**

---

## The Problem Being Solved

Modern AI systems are powerful but stateless, ephemeral, and passive.

- They forget everything between sessions.
- They answer questions but do not take actions.
- They do not know who you are beyond the current conversation.
- They do not operate your computer.
- They do not learn from what worked and what failed.
- They are cloud-dependent, opaque, and not yours.

Immortality exists to solve this. It is a personal AI runtime that:

1. **Persists** — survives restarts, remembers context across sessions, grows over time.
2. **Acts** — executes real tasks on the computer, not just generates text.
3. **Learns** — records outcomes, builds procedural memory, improves with use.
4. **Observes** — perceives the screen, filesystem, git state, and running processes.
5. **Reasons** — decomposes complex goals into plans, adapts when plans fail.
6. **Verifies** — checks its own work before claiming success.
7. **Protects** — never bypasses user permissions, never takes destructive actions silently.

---

## What "AGI-Like" Means for This Project

This project does not claim to be building AGI. The term "AGI-like" refers to the following architectural properties:

| Property | Meaning |
|---|---|
| **General task handling** | One system handles diverse task types without per-task specialization |
| **Composable capability** | Intelligence emerges from combining memory, reasoning, tools, and observation |
| **Self-improvement** | The system gets better by recording and learning from outcomes |
| **Long-horizon execution** | Multi-step tasks spanning hours, not just single turns |
| **Persistent identity** | The system knows who the user is across all sessions |
| **World model** | The system maintains a structured representation of the user's environment |

What this project is NOT claiming:
- Human-level reasoning across all domains
- Emergent general intelligence
- Unsupervised autonomous operation without permissions
- Self-modification or recursive self-improvement

---

## Precise System Definitions

These terms are used precisely throughout this documentation:

| Term | Definition |
|---|---|
| **Chatbot** | A system that responds to text with text. Stateless. No tools. |
| **AI assistant** | A chatbot with memory of the current session. May have limited tools. |
| **Agent** | A system that uses tools to take actions in pursuit of a goal. Single-turn or short-horizon. |
| **Agent harness** | Infrastructure that manages the loop: model → tool → observe → continue. |
| **Personal AI** | A system with persistent identity, user-specific memory, and local context. |
| **JARVIS** | A personal AI that operates the user's computer, acts proactively, and feels like a capable partner. |
| **AGI-like system** | A system with general task handling, world modeling, self-improvement, and persistent identity. Not AGI. |
| **AGI** | Human-level reasoning across arbitrary domains. Out of scope for this project. |

**Immortality targets: AGI-like system, with JARVIS as the experience target.**

---

## Capability Model (Final Vision)

The final vision is described as a capability model, not a feature list.

### IDENTITY
Immortality knows the user's name, projects, preferences, working style, and goals. This identity persists across restarts, devices, and sessions. It is not reconstructed from a system prompt — it is stored, retrieved, and updated continuously.

### MEMORY
Immortality maintains multiple memory layers:
- **Working memory** — current task and conversation context
- **Episodic memory** — past interactions and task outcomes
- **Semantic memory** — facts, concepts, and domain knowledge
- **Procedural memory** — how tasks are typically performed
- **Project memory** — knowledge about specific codebases and projects
- **Preference memory** — the user's stated and inferred preferences

### REASONING
Immortality reasons over problems using a capable language model (currently Kimi K3 via NVIDIA NIM). Reasoning is grounded in retrieved context from memory, not invented.

### PLANNING
For complex goals, Immortality creates an explicit plan: a sequence of steps with expected outcomes, dependencies, checkpoints, and verification criteria. Plans are stored as durable task objects, not held only in the model's context window.

### PERCEPTION
Immortality perceives the user's digital environment:
- Filesystem and project structure
- Screen content (via screenshots and OCR/vision)
- Browser state (via Playwright accessibility tree)
- Terminal output
- Git state
- Running processes and applications

### ACTION
Immortality takes real actions:
- Edit files, create projects, run tests
- Execute terminal commands (with permission)
- Control the browser
- Control Windows applications via accessibility APIs
- Use voice to interact with the user
- Search the web and synthesize information

### COMPUTER CONTROL
Immortality can operate the user's computer using a layered approach:
1. Structured APIs (git, filesystem, browser DOM)
2. Windows UI Automation (accessibility tree)
3. Vision-based targeting (screenshot → vision model → action)
4. Mouse/keyboard as last resort

### KNOWLEDGE
Immortality maintains indexed knowledge about:
- The user's codebase (via TurboVec hybrid search + code graph)
- Imported documentation
- Learned facts from past interactions
- Self-reflection records from task execution

### LEARNING
Immortality learns from every task:
- Records outcomes (success/failure) with details
- Stores structured reflections ("what broke, what fixed it")
- Builds procedural memory from repeated patterns
- Improves retrieval quality over time

### WORLD MODEL
Immortality maintains a structured model of the user's digital world:
- Projects and their state
- Files and their relationships
- Applications and services in use
- People and accounts
- Ongoing goals and tasks

### TOOLS
Immortality has access to ~40 native tools (filesystem, git, terminal, browser, web search, documents, databases, Docker) and a growing set of computer control primitives. Tools are permissioned, validated, and traced.

### AUTONOMY
Immortality can execute multi-step tasks autonomously within explicit permissions. Tasks are durable — they survive process restarts. The system can pause, resume, checkpoint, rollback, and retry.

### PROACTIVITY
Eventually, Immortality will notice relevant events (calendar, git activity, build failures, deadlines) and surface useful actions before being asked.

### VOICE
Immortality supports natural voice interaction via local Whisper STT and Windows SAPI TTS — no cloud APIs required.

### VISION
Immortality will use a local vision model (Qwen-VL class) to understand screenshots and screen content, enabling computer control without relying on coordinates.

### SECURITY
The LLM never bypasses the permission layer. All mutating and destructive actions require explicit user approval unless the system is in AUTONOMOUS or DEVELOPER mode. Computer control actions are permissioned separately. Prompt injection from web content is detected and quarantined.

### SELF-VERIFICATION
Every coding task includes a verification gate: diff review, syntax checks, and test execution. The system does not claim success until verification passes.

### FAILURE RECOVERY
Every significant task has checkpoints. If a task fails or the process crashes, the system can restore from the last checkpoint and resume from the current step.

### CONTINUAL IMPROVEMENT
The system records task outcomes, builds experience datasets, and uses them to improve future decisions. Eventually, this data feeds fine-tuning pipelines.

---

## What Is Achievable Today vs Long-Term

### Achievable now (current codebase):
- Natural language chat with local or cloud LLM
- Multi-step tool loop with confirmations and verification
- Hybrid semantic + BM25 code retrieval
- Autonomous coding workflow (plan → code → review → verify)
- Voice I/O (local Whisper + Windows SAPI)
- Browser automation (Playwright, accessibility-tree based)
- Git, Docker, filesystem, terminal, web search tools
- Permission system (SAFE/ASSISTED/AUTONOMOUS/DEVELOPER)
- Session persistence and resume
- Experience memory and self-reflection
- HUD frontend

### Achievable with focused engineering (6-12 months):
- Full task persistence across restarts (durable Task objects)
- Screen capture and vision-based understanding
- Windows app control via UI Automation (pywinauto)
- Voice routing to the full agent loop (not just fast chat)
- Proactive event monitoring
- World model (project/task/file relationship graph)

### Research-phase objectives (1-3 years):
- True long-horizon autonomous operation
- Cross-device operation
- Emergent procedural skill learning
- Self-directed knowledge acquisition
- Advanced world modeling

---

## Non-Negotiable Principles

1. Immortality owns the core runtime. No external framework is the identity of the system.
2. The user's data stays local. Cloud APIs are used for intelligence, not for storage.
3. Permissions are enforced below the LLM. The model cannot bypass safety constraints.
4. Every autonomous action is observable and reversible where possible.
5. The system is honest about its capabilities. It does not claim to do what it cannot.
6. Existing working systems are not rewritten for architectural aesthetics.
7. Complexity is added only when it solves a real problem.
