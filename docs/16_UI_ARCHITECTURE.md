# 16 — UI Architecture

## Current UI State

Immortality has two frontends:

1. **CLI** (`main.py`) — Rich terminal with prompt_toolkit completion, voice mode, slash commands
2. **HUD** (`frontend/immortility_hud.html`) — Single-page browser-based JARVIS interface on port 8765

Both frontends share the same backend: the HUD server (`tools/hud_launcher.py` + `tools/hud_agent.py`) is served via Python's `http.server` on :8765.

---

## HUD Architecture (Current)

The HUD is a single HTML file with:
- Inline CSS (no build step required)
- Vanilla JavaScript (no framework)
- WebSocket polling via `setInterval()` to `/state`, `/vitals` endpoints
- Three views: Core (face + chat + vitals), Work (task rail), Focus (chat only)

### Components

| Component | Location in HTML | Status | Backend |
|---|---|---|---|
| JARVIS face (SVG, animated) | `#core` `.face-wrap` | ✅ Working | None (CSS animations) |
| Chat panel | `.chat` `#log` | ✅ Working | `/chat` endpoint |
| Voice input button | `.mic-btn` | ✅ Working | `/listen` endpoint |
| Stop button | `.stop-btn` | ✅ Working | `/cancel` endpoint |
| File attachment | `.attach-btn` | ✅ Working | `/upload` endpoint |
| Permission confirm bar | `.confirm-bar` | ✅ Working | `/state` + `/confirm` |
| System vitals (CPU/RAM/VRAM) | vitals bars | ✅ Working | `/vitals` endpoint |
| Event log | `#eventLog` | ✅ Working | `/events` endpoint |
| Task rail | `.task-rail` | ⚠️ Cosmetic only | Not wired to real tasks |
| Calendar widget | `.cal-grid` | ✅ Working | Client-side only |
| Todo list | `.todo-list` | ✅ Working | `/todos` endpoints |
| Clock | `#topClock` | ✅ Working | Client-side |
| Model hint | `#modelHint` | ✅ Working | `/state` endpoint |
| Permission mode indicator | footer | ✅ Working | `/state` endpoint |

### Face States
The JARVIS face has animated states:
- `idle` — breathing animation
- `thinking` — eye gaze shift animation
- `speaking` — animated mouth (talk animation)
- `listening` — ring pulse animation

These are driven by CSS classes set via `tools/hud_state.py::set_face(state)`.

---

## Backend Server Architecture

```python
# tools/hud_launcher.py — starts the server
# tools/hud_agent.py — handles /chat POST

# Endpoints (implemented in hud_agent.py + tools/hud_*.py):
GET  /                    → immortility_hud.html
GET  /state               → AgentState JSON (face, model, mode, pending)
GET  /vitals              → CPU/RAM/VRAM metrics
GET  /events              → Recent events from event bus
POST /chat                → Send message, stream response
POST /listen              → Trigger microphone recording
POST /cancel              → Cancel current execution
POST /confirm             → Confirm pending action (yes/no)
POST /upload              → Upload document/image
GET  /knowledge           → Knowledge engine stats
POST /index               → Index a project
GET  /todos               → Todo list
POST /todos               → Add/complete todo
```

The HUD polls these endpoints via `setInterval` (1-2 second intervals). This is not true WebSocket streaming but works adequately for the current use case.

---

## UI Design Principles

1. **The backend is the product.** The UI shows what the backend does. UI improvements are secondary to backend capability.

2. **Do not redesign the HUD for aesthetics.** The current JARVIS aesthetic is correct. Changes should add information density, not visual redesign.

3. **The CLI remains the primary interface** for power users and voice-first interaction. The HUD is a visual companion.

4. **No JavaScript framework.** The current vanilla JS + CSS is maintainable and has zero build tooling. Adding React/Vue would add complexity for no gain.

5. **Progressive enhancement.** New features appear in the HUD after the backend is stable, not before.

---

## Required UI Changes for JARVIS MVP (Phase 1-6)

### Priority 1: Task Timeline (Phase 1)

The most important UI change. The task rail currently shows static cosmetic items.

**Target:**
```html
<!-- Task rail shows real Task objects -->
<ul class="task-rail" id="taskRail">
    <!-- Populated by polling /task/{id}/steps -->
    <li class="done">Created plan: 5 steps</li>
    <li class="done">git_status: 2 modified files</li>
    <li class="now">edit_file: core/auth.py</li>
    <li>Run pytest</li>
    <li>Verify changes</li>
</ul>
```

**Backend endpoint needed:** `GET /task/current/steps` → list of `{description, status, latency_ms}`

### Priority 2: Permission Prompt Enhancement (Phase 2)

The confirm bar already works. Enhance it for computer control:

```html
<!-- Show screenshot of what will be clicked -->
<div class="confirm-bar show">
    <p>Click "Run Tests" button in VS Code?</p>
    <img class="screen-preview" src="/screen/region?...">  <!-- small screenshot -->
    <button class="yes">Yes</button>
    <button class="no">No</button>
</div>
```

### Priority 3: Screen Preview Panel (Phase 3)

Add a small screen preview when computer control is active:

```html
<!-- In the side panel, add a screen preview -->
<div class="panel screen-preview" id="screenPreview">
    <h2>SCREEN</h2>
    <img id="screenCapture" src="" alt="Current screen">
    <p id="screenDescription" class="muted"></p>
</div>
```

Updated on tool call to `screenshot()` or `screen_analyze()`.

### Priority 4: Agent Activity (Phase 4+)

Show which tool is currently executing:

```html
<!-- Live activity indicator in the top bar -->
<span id="agentActivity" class="activity">
    <!-- Changes to: "→ edit_file core/auth.py" during execution -->
</span>
```

### Priority 5: Voice Status (Phase 4)

Improve voice feedback visibility:
- Show STT confidence score
- Show which agent path was taken (fast/agent/project)
- Show spoken acknowledgment text

---

## HUD Information Architecture (Target)

```
┌────────────────────────────────────────────────────────────────┐
│  TOPBAR: IMMORTALITY  [Agent: kimi-k3]  [Mode: ASSISTED]  09:45│
├──┬─────────────────────────────────────────────────────────────┤
│  │  LEFT: FACE PANEL          CENTER: CHAT           RIGHT     │
│  │                            ─────────────────      PANEL    │
│  │  [JARVIS SVG Face]         [Chat log with        VITALS     │
│  │  (thinking/speaking/       markdown rendering]   CPU/RAM    │
│  │   idle/listening)                                VRAM/GPU   │
│  │                            [Confirmation bar    ──────────  │
│  │  [TASK RAIL]               when pending]        CALENDAR    │
│  │  ✓ Created plan                                 ──────────  │
│  │  ✓ git_status              [Chat input]         TODOS      │
│  │  → edit_file               [Mic] [Stop]         ──────────  │
│  │  □ Run tests               [Attach] [Send]      EVENT LOG  │
│  │  □ Verify                                                   │
│  │                                                 [Screen     │
│  │  [SCREEN PREVIEW]          [Agent activity:     preview    │
│  │  [thumbnail of screen]     → edit_file 23ms]    when active]│
├──┴─────────────────────────────────────────────────────────────┤
│  FOOTER: [Mode: ASSISTED]  [Turn: 47ms TTFT]  [Kimi K3]       │
└────────────────────────────────────────────────────────────────┘
```

---

## No Framework Recommendation

The current HUD is vanilla HTML/CSS/JS. This is correct for this use case.

**Why no React/Vue/Svelte:**
- No build tooling required — the HTML file is the deployment artifact
- Single developer, no team convention benefits
- No package.json/node_modules for the frontend
- CSS animations are already in the file and working
- The HUD is a companion display, not a complex SPA

If the HUD grows to need:
- Complex state management
- Multiple routes
- Component reuse across > 10 components
- A team working on it simultaneously

...then revisit the framework decision. Not before.

---

## CLI Architecture (Current)

`main.py` provides:
- `prompt_toolkit` for inline completion of slash commands
- `rich` for formatted terminal output (panels, markdown, color)
- `/talk` — continuous voice loop
- `/open <path>` — project indexing
- `/hud` — open HUD in browser
- `/doctor` — model fleet health
- `/capabilities` — capability report
- `/mode <mode>` — permission mode
- `/memory` — knowledge engine stats
- `/resume` / `/handoff` — session continuity
- `/clear` — clear state

The CLI is a thin shell. All business logic is in `core/`. There is no CLI-specific business logic to migrate.

---

## Accessibility

The HUD has basic accessibility:
- `aria-label` on interactive buttons
- `aria-live` regions for dynamic content (the SVG face has alt text)
- Keyboard-navigable input

WCAG full compliance would require manual testing with screen readers. The current implementation targets functional accessibility, not certification.
