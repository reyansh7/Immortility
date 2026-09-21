# 10 — World Model

## What Is the World Model?

The world model is Immortality's structured representation of the user's digital environment. It answers questions that RAG cannot:

- What projects does the user own? What is their current state?
- What applications are currently open?
- What task was interrupted an hour ago?
- What changed in the codebase since we last worked on it?
- What is the relationship between Project A and Project B?
- What was the last thing we did in this project?
- What files has the user been editing this week?

RAG (TurboVec + hybrid search) answers: "which code chunk is most similar to this query?" — semantics.

The world model answers: "what is the state of the user's world right now?" — structure and time.

---

## Why This Is Not RAG

| Question | RAG Answer | World Model Answer |
|---|---|---|
| "What projects do I have?" | Returns chunks mentioning projects | Live list from filesystem scan |
| "What's the state of Immortality?" | Returns relevant code snippets | Project node: last opened, active tasks, git status |
| "What did I work on yesterday?" | Cannot answer reliably | Episodic task records filtered by date |
| "What apps are open?" | Cannot answer | Live window enumeration from computer tool |
| "What depends on auth_module.py?" | Code graph query | Relationships edge in world model |
| "What's my current goal?" | May find a mention | Goal node linked to active task |

---

## World Model Data Model

### Node Types

```python
class NodeType(Enum):
    USER = "user"
    PROJECT = "project"
    FILE = "file"
    DIRECTORY = "directory"
    APPLICATION = "application"
    WEBSITE = "website"
    PERSON = "person"
    TASK = "task"
    GOAL = "goal"
    DOCUMENT = "document"
    SERVICE = "service"       # GitHub, Notion, email, calendar
    DEVICE = "device"
    REPOSITORY = "repository"
```

### Edge Types (Relationships)

```python
class EdgeType(Enum):
    OWNS = "owns"                   # user owns project
    WORKS_ON = "works_on"           # user works on project/task
    CONTAINS = "contains"           # project contains file
    DEPENDS_ON = "depends_on"       # file depends on file
    CREATED = "created"             # user created file/task
    COMPLETED = "completed"         # user completed task
    USES = "uses"                   # user uses application/service
    KNOWS = "knows"                 # user knows person
    BOOKMARKED = "bookmarked"       # user bookmarked website
    RELATED_TO = "related_to"       # general relationship
    PART_OF = "part_of"             # task part of project
    BLOCKED_BY = "blocked_by"       # task blocked by task
    REFERENCES = "references"       # document references file
```

### Node Schema

```python
@dataclass
class WorldNode:
    id: str                  # UUID or derived key (e.g. "project:immortality1")
    type: NodeType
    name: str
    description: str = ""
    properties: dict = {}    # type-specific attributes
    state: str = "active"    # active | inactive | unknown | deleted
    last_seen: datetime | None = None
    last_modified: datetime | None = None
    confidence: float = 1.0  # 0-1, decays for unverified claims
    source: str = ""         # how was this learned (scan, user, inference)
    created_at: datetime = None
```

### Type-Specific Properties

**PROJECT node:**
```python
{
    "path": "C:\\Users\\reyan\\Desktop\\Projects\\immortality1",
    "language": "Python",
    "framework": "custom",
    "git_remote": "https://github.com/...",
    "last_opened": "2026-09-20T14:32:00Z",
    "chunk_count": 14000,
    "active_task_id": "task_abc123",
}
```

**FILE node:**
```python
{
    "path": "core/action_engine.py",
    "project_id": "project:immortality1",
    "lines": 741,
    "language": "Python",
    "last_modified": "2026-09-18T10:00:00Z",
    "symbols": ["execute_action", "agent_step", "resume_confirmed_pending"],
}
```

**APPLICATION node:**
```python
{
    "executable": "Code.exe",
    "pid": 12345,
    "is_active": True,
    "window_title": "core/action_engine.py - immortality1 - Visual Studio Code",
    "last_active": "2026-09-21T09:45:00Z",
}
```

**TASK node:**
```python
{
    "task_id": "task_abc123",
    "goal": "Fix the failing authentication tests",
    "status": "completed",
    "project_id": "project:immortality1",
    "started_at": "2026-09-20T10:00:00Z",
    "completed_at": "2026-09-20T10:32:00Z",
    "outcome": "success",
    "artifacts": ["core/auth.py", "tests/test_auth.py"],
}
```

---

## How the World Model Is Built and Updated

### Initial Population
When the system starts:
1. `core/desktop_scanner.py` provides the live filesystem structure → creates PROJECT nodes
2. `memory/project_memory.py` provides previously known project metadata → enriches PROJECT nodes
3. `computer_tool.list_windows()` (Phase 2) provides open applications → creates APPLICATION nodes

### Continuous Updates
During task execution, the world model is updated:
- When a project is opened: PROJECT node updated with `last_opened`
- When a file is edited: FILE node updated with `last_modified`, artifacts linked to TASK
- When a task completes: TASK node marked `completed`, outcome recorded
- When a screenshot is taken (Phase 3): APPLICATION nodes updated with active window state
- When git status is read: PROJECT node updated with `dirty` flag, recent commit info

### Inference
Some world model knowledge is inferred from existing data:
- If `project_memory.py` shows a project at a path → create PROJECT node
- If `experience_memory.py` shows a task about a project → create TASK→PROJECT edge
- If AST analysis shows file A imports file B → create FILE→FILE DEPENDS_ON edge

### Confidence Decay
Stale facts lose confidence over time:
- APPLICATION nodes with `last_seen` > 1 hour: `state = "unknown"`
- FILE nodes not accessed in 30 days: `confidence = max(0.5, confidence * 0.95)`
- Facts derived from inference (not direct observation): `confidence = 0.7`

---

## World Model Storage

Storage: Extend `knowledge/knowledge_graph_db.py` with world model tables.

```sql
-- World model nodes
CREATE TABLE world_nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    properties TEXT DEFAULT '{}',  -- JSON
    state TEXT DEFAULT 'active',
    last_seen TEXT,
    last_modified TEXT,
    confidence REAL DEFAULT 1.0,
    source TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

-- World model edges
CREATE TABLE world_edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES world_nodes(id),
    target_id TEXT NOT NULL REFERENCES world_nodes(id),
    relation TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',  -- JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_edges_source ON world_edges(source_id);
CREATE INDEX idx_edges_target ON world_edges(target_id);
CREATE INDEX idx_nodes_type ON world_nodes(type);
CREATE INDEX idx_nodes_state ON world_nodes(state);
```

SQLite is sufficient. The world model for a personal AI will have hundreds to low thousands of nodes, not millions.

---

## World Model vs RAG — Integration

These are complementary, not competing:

| Query type | Use |
|---|---|
| "What is the pattern for handling auth in this codebase?" | RAG → TurboVec semantic search |
| "What projects do I have?" | World model → node query |
| "What is the architecture of immortality1?" | World model → project node properties |
| "What broke in tests last time?" | World model → task nodes filtered by outcome |
| "Find the function that handles routing" | RAG → code graph + TurboVec |
| "What was I working on yesterday?" | World model → task nodes filtered by date |

### Retrieval Pattern

For every agent call, context is assembled from both:
```python
# From knowledge/engine.py (Phase 5)
def get_personal_context(query: str) -> str:
    # 1. World model: relevant nodes (project, recent tasks, active apps)
    world_context = world_model.relevant_nodes(query, limit=5)
    
    # 2. RAG: semantic chunks from codebase/docs
    rag_context = hybrid_search.search(query, n_results=8)
    
    # 3. Recent episodes: related past tasks
    episodes = experience_memory.get_relevant(query, limit=3)
    
    # 4. Preferences: relevant user preferences
    prefs = preference_memory.get_relevant(query)
    
    return assemble_personal_context(world_context, rag_context, episodes, prefs)
```

---

## World Model Queries

Example queries the world model should answer:

```python
# Structural queries
world.get_node("project:immortality1")
world.get_children("project:immortality1", relation="CONTAINS", type="FILE")
world.get_related("file:core/action_engine.py", relation="DEPENDS_ON")

# Temporal queries
world.get_recent_tasks(days=7, status="completed")
world.get_active_application()
world.get_last_worked_project()

# State queries
world.get_active_tasks()
world.get_blocked_tasks()
world.get_stale_nodes(confidence_below=0.5)

# Relationship queries
world.get_path("user", "task:abc123")  # how did user create this task
world.get_projects_by_language("Python")
world.get_tasks_for_project("project:immortality1", limit=5)
```

---

## Relationship to the JARVIS Experience

The world model is what makes JARVIS feel like it *knows* you:

- "Open Immortality" → world model provides the exact path without asking
- "Continue what you were doing" → world model provides the interrupted task
- "What changed since this morning?" → world model + git tool provides a meaningful diff summary
- "Find the auth code" → world model + RAG together locate both the concept and the implementation
- "What apps do I have open?" → world model (populated by computer tool) answers instantly
