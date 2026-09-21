# 02 — AGI Capability Model

## Capability Levels

Immortality is building through a defined progression. Each level adds real new capability — it is not marketing graduation.

---

### LEVEL 0 — STATELESS CHATBOT
**Definition:** Responds to text with text. No memory beyond the context window. No tools.

**Immortality status:** Surpassed. Immortality has had tools and memory since early phases.

---

### LEVEL 1 — TOOL-USING ASSISTANT
**Definition:** Can invoke external tools (search, filesystem, APIs) in response to requests. Single-turn. No task persistence.

**Immortality status:** Surpassed. ~40 tools registered. Permission system. Confirmation gates.

**What distinguishes Level 1:** The agent acts, but the action is one tool call per turn. There is no multi-step plan.

---

### LEVEL 2 — MULTI-STEP AGENT
**Definition:** Can decompose a goal into multiple tool calls across multiple LLM turns. Has a verification mechanism. Can retry on failure.

**Immortality status:** Present. `execute_action()` tool loop with step caps, no-progress detection, verification gate. `run_coding_loop()` with Planner→Coder→Reviewer→Executor→Reflector.

**What distinguishes Level 2:** The agent executes a sequence of interdependent actions, not just a single tool call.

---

### LEVEL 3 — PERSONAL AGENT
**Definition:** Knows who the user is. Remembers past interactions. Has project context. Learns from outcomes. Operates on the user's local environment.

**Immortality status:** Partially present.
- User identity: exists (user_profile.py)
- Project memory: exists (project_memory.py)
- Experience/outcome learning: exists (experience_memory.py, self_reflection.py)
- Persistent codebase knowledge: exists (TurboVec + knowledge graph)
- **Missing:** Cross-session task continuity. Tasks do not survive restarts with full observation history.

**What distinguishes Level 3:** The system is yours, not generic.

---

### LEVEL 4 — JARVIS (THE TARGET)
**Definition:** Operates the computer. Long-horizon task execution that survives restarts. Observes the screen. Controls applications. Acts on voice commands with full agent capability (not just fast chat). Proactively surfaces relevant information.

**Immortality status:** Partially present.
- Voice I/O: exists (voice_io.py) but routes to fast_chat only, not full agent loop
- Browser automation: exists (browser_agent.py, Playwright)
- Git/filesystem/terminal tools: exist
- **Missing:** 
  - Computer control (mouse, keyboard, window management, app launching via pywinauto/UIA)
  - Vision-based screen understanding
  - Voice routing to full agent loop
  - Durable task persistence (tasks.db)
  - Proactive event monitoring

**What distinguishes Level 4:** The system does things without the user being at the keyboard.

---

### LEVEL 5 — GENERAL PERSONAL INTELLIGENCE
**Definition:** Handles previously unseen task types by composing existing primitives. Maintains a living world model of the user's digital environment. Learns new procedures from observation. Operates across multiple applications in coordinated workflows.

**Immortality status:** Not yet built. This is the primary long-term engineering target.

**What distinguishes Level 5:** Generalization without pre-programming every task type.

---

### LEVEL 6 — AGI RESEARCH FRONTIER
**Definition:** Human-level reasoning across arbitrary domains. Self-directed learning. Novel knowledge synthesis beyond training data.

**Immortality status:** Out of scope for current engineering. May become relevant in 3-5 years as model capability advances.

---

## Current Level Assessment

**Immortality is currently between Level 2 and Level 3**, with specific Level 4 capabilities in narrow domains (coding, web, files) but missing the general Level 4 computer control and task durability.

The gap to full Level 4 (JARVIS) requires:
1. Durable task persistence (`tasks.db`)
2. Computer control (pywinauto + screen capture)
3. Vision-based screen understanding (Qwen-VL)
4. Voice routing to full agent loop
5. Proactive event monitoring

---

## Capability Matrix

| Capability | Current | Required for L4 | Difficulty | Status |
|---|---|---|---|---|
| **Reasoning** | Kimi K3 / Ollama | Kimi K3 | Low | ✅ Active (needs API key) |
| **Memory — working** | state.json | tasks.db | Medium | ⚠️ Fragile (flat file) |
| **Memory — episodic** | experience_memory.py | Expand schema | Medium | ✅ Functional |
| **Memory — semantic** | TurboVec hybrid search | Keep | Low | ✅ Functional |
| **Memory — procedural** | experience + reflection | Expand | Medium | ⚠️ Partial |
| **Memory — project** | project_memory.py | World model | High | ✅ Functional |
| **Identity** | user_profile.py | Expand | Medium | ⚠️ Basic |
| **World model** | Desktop scanner | Full graph | High | ❌ Not built |
| **Planning** | LLM planner + heuristic | Task graph | Medium | ⚠️ Partial |
| **Task durability** | workflow.db | tasks.db | Medium | ⚠️ Partial |
| **Task recovery** | checkpoint_manager | Wire to tasks | Medium | ⚠️ Exists, not wired |
| **Tool use** | ~40 tools | Computer tools | Medium | ✅ Functional |
| **Computer control — structured** | brightness/volume only | pywinauto + UIA | Medium | ❌ Not built |
| **Computer control — vision** | None | Qwen-VL | High | ❌ Not built |
| **Browser automation** | Playwright (a-tree) | Keep + expand | Low | ✅ Functional |
| **Coding** | Full loop | Keep | Low | ✅ Functional |
| **Voice — STT** | Whisper offline | Keep | Low | ✅ Functional |
| **Voice — TTS** | SAPI offline | Keep | Low | ✅ Functional |
| **Voice — full agent** | fast_chat only | Route to agent | Low | ❌ Not wired |
| **Vision — screen** | None | Qwen-VL model | High | ❌ Not built |
| **Vision — model** | Unset | OLLAMA_VISION_MODEL | Low | ⚠️ Config only |
| **Learning — outcomes** | ExperienceMemory | Keep | Low | ✅ Functional |
| **Learning — procedural** | Self-reflection | Expand | Medium | ⚠️ Partial |
| **Learning — fine-tuning** | Dataset builder | LoRA pipeline | Very High | ❌ Not built |
| **Self-verification** | Diff review + CI | Keep | Low | ✅ Functional |
| **Autonomy — ASSISTED** | Confirmations | Keep | Low | ✅ Functional |
| **Autonomy — AUTONOMOUS** | Permission mode | Keep | Low | ✅ Functional |
| **Proactivity** | None | Event monitor | High | ❌ Not built |
| **Multi-step execution** | Tool loop + workflow | Task graph | Medium | ✅ Functional |
| **Failure recovery** | Checkpoints | Wire to tasks | Medium | ⚠️ Partial |
| **Personalization** | Preferences | World model | High | ⚠️ Basic |
| **Security — permissions** | 4-mode system | Keep + expand | Low | ✅ Functional |
| **Security — prompt injection** | None | Input scanner | High | ❌ Not built |
| **Security — sandboxing** | CWD restriction | Docker sandbox | High | ⚠️ Partial |
| **External service integration** | None planned | MCP (later) | Medium | ❌ Not built |
| **Observability** | TraceEvent JSONL | Task timeline UI | Medium | ⚠️ Partial |

Legend: ✅ Functional | ⚠️ Partial/needs work | ❌ Not built

---

## The Capabilities That Create the JARVIS Experience

These are ordered by impact on the actual JARVIS experience, not by implementation difficulty.

| Rank | Capability | Why It Matters | Dependency |
|---|---|---|---|
| 1 | **Persistent task state** | "JARVIS, finish what you were doing" only works with durable tasks | None |
| 2 | **Voice → full agent** | The defining interaction model — speak, it acts | Task persistence |
| 3 | **Screen understanding** | JARVIS observes, not just executes blind | Vision model configured |
| 4 | **Computer control** | Opening apps, clicking, typing in any window | Screen understanding |
| 5 | **Kimi K3 as brain** | Dramatically better reasoning for complex tasks | NVIDIA_API_KEY |
| 6 | **Personal context injection** | Every decision informed by who you are | Memory expansion |
| 7 | **Failure recovery** | Long tasks must survive failures | Task persistence |
| 8 | **Proactive monitoring** | "I noticed the tests are failing" | Event monitor |
| 9 | **World model** | Knows all your projects, their states, relationships | Task + project data |
| 10 | **Procedural learning** | Gets better at your workflows over time | Experience memory |

---

## Capability Dependency Graph

```
LEVEL 4 JARVIS
    │
    ├── Task persistence (tasks.db)
    │       └── enables: failure recovery, voice continuity, proactivity
    │
    ├── Voice → agent routing
    │       └── requires: full agent loop (existing), voice I/O (existing)
    │
    ├── Computer control
    │       ├── requires: pywinauto (new), screen capture (new)
    │       └── enhanced by: vision model (new)
    │
    ├── Vision / screen understanding
    │       ├── requires: OLLAMA_VISION_MODEL configured
    │       └── enables: computer control quality, world model updates
    │
    ├── Kimi K3 as brain
    │       └── requires: NVIDIA_API_KEY (config only)
    │
    └── Proactive intelligence
            ├── requires: event monitor (new), task persistence
            └── enhanced by: world model (new)
```
