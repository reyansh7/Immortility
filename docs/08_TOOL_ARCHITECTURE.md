# 08 — Tool Architecture

## Design Philosophy

Tools are primitives, not agents. A tool does one specific thing, takes structured arguments, returns a structured result, and is always invoked through the permission layer. Tools do not have internal LLM calls, do not make decisions, and do not chain other tools.

The distinction:
- **Tool:** `git_status(cwd)` → returns current branch, staged/unstaged files
- **Agent behavior:** Read git status, then decide which files to look at, then edit them, then verify

---

## Tool Registry

All tools are registered in `tools/tool_registry.py`. The registry is a process-wide singleton.

### Registration Format
```python
registry.register(
    name="tool_name",
    func=ToolClass.method,
    description="One line description for the agent prompt",
    args_schema={"arg_name": "type description"},
    permissions={"read": bool, "write": bool, "network": bool, "destructive": bool},
    tags=["category1", "category2"],
)
```

### Current Tool Count
~40 tools registered across categories. See the full list in `tools/tool_registry.py::setup()`.

---

## Tool Categories

### Filesystem Tools
| Tool | Description | Risk |
|---|---|---|
| `read_file` | Read file content with optional line numbers | LOW |
| `write_file` | Write content to file | MEDIUM |
| `create_file` | Create new file (fails if exists) | MEDIUM |
| `edit_file` | Search-and-replace in file | MEDIUM |
| `replace_lines` | Replace line range in file | MEDIUM |
| `append_file` | Append to file | MEDIUM |
| `insert_before` / `insert_after` | Insert around target text | MEDIUM |
| `replace_regex` | Regex replacement in file | MEDIUM |
| `delete_block` | Delete text block from file | MEDIUM |
| `delete_file` | Delete a file | HIGH |
| `list_directory` | List directory contents | LOW |
| `rename_symbol` | Rename symbol in file | MEDIUM |
| `find_symbol` | Find symbol in project | LOW |
| `query_code_graph` | Query structural graph | LOW |
| `find_references` | Find symbol references | LOW |
| `generate_diff` / `apply_patch` / `validate_patch` | Patch operations | MEDIUM-HIGH |

### Terminal / Command Tools
| Tool | Description | Risk |
|---|---|---|
| `run_command` | Execute shell command (timeout/cwd/limits) | HIGH |
| `open_application` | Launch application by name | MEDIUM |
| `get_system_status` | CPU/memory/disk info | LOW |
| `list_top_processes` | Running processes list | LOW |
| `kill_process` | Terminate process by PID | HIGH |

### Browser Tools
| Tool | Description | Risk |
|---|---|---|
| `open_url` | Navigate to URL | LOW |
| `get_current_url` | Current page URL | LOW |
| `get_page_title` | Current page title | LOW |
| `get_page_text` | Extract readable text | LOW |
| `scrape_page` | Scrape HTML from URL | LOW |
| `inspect_url` | Fetch URL and return text | LOW (NETWORK) |
| `browser_goal` | Run goal-based browser automation | MEDIUM |
| `list_tabs` / `switch_tab` | Tab management | LOW |
| `take_screenshot` | Save page screenshot | LOW |
| `search_google` / `web_search` | Web search | LOW (NETWORK) |

### Git Tools (22 tools)
| Category | Tools | Risk |
|---|---|---|
| Read-only | `git_status`, `git_diff`, `git_log`, `git_show`, `git_remote`, `git_branch` (list) | LOW |
| Confirmed mutating | `git_checkout`, `git_switch`, `git_add`, `git_commit`, `git_stash`, `git_fetch` | MEDIUM |
| Always confirmed | `git_push`, `git_reset`, `git_branch` (delete) | HIGH |

### Document Tools
| Tool | Description | Risk |
|---|---|---|
| `extract_document` | Parse PDF/DOCX/PPTX/XLSX/CSV | LOW |

### Database Tools
| Tool | Description | Risk |
|---|---|---|
| `db_list_connections` | List configured connections | LOW |
| `db_query` | Read-only SQL (SELECT/WITH) | LOW |
| `db_execute` | Write SQL (confirmed) | MEDIUM-HIGH |
| `db_mongo_find` | MongoDB find (read-only) | LOW |

### Docker Tools
| Tool | Description | Risk |
|---|---|---|
| `docker_ps`, `docker_images`, `docker_inspect`, `docker_logs`, `docker_info`, `docker_version` | Read-only | LOW |
| `docker_stop` | Stop container (confirmed) | MEDIUM |
| `docker_rm` | Remove container (destructive) | HIGH |

### Discovery Tool
| Tool | Description | Risk |
|---|---|---|
| `discover_tools` | Find tools by query/tag without LLM | LOW |

---

## Tool Execution Pipeline

```
Agent generates: {"tool": "edit_file", "args": {...}}
    ↓
core/json_utils.py::parse_llm_json()  ← fixes malformed JSON
    ↓
editing/patch_args.py::normalize_patch_args()  ← normalize edit args
    ↓
core/permissions.py::decide(tool_name, args)
    → DENY: return denial message
    → CONFIRM: store pending_action, return confirmation prompt
    → ALLOW: continue
    ↓
tools/tool_registry.py::execute(tool_name, args)
    ↓
func(**args)  ← actual tool implementation
    ↓
{"status": "success"|"error", "tool": name, "result": value}
```

---

## Tool Schema Design

Every tool has a schema that serves two purposes:
1. Documentation for the agent prompt
2. Argument validation at call time

```python
# Example: git_commit schema
args_schema = {
    "message": "string",
    "cwd": "string (optional)",
    "amend": "bool (optional)",
}
```

Current schemas use string descriptions, not JSON Schema. A future improvement (Phase 10) would add proper JSON Schema validation to catch type errors before execution.

---

## Tool Discovery

The `discover_tools` meta-tool allows the agent to find the right tool for a task:

```python
discover_tools(query="git status", tags="git")
# Returns: matching tools with full schemas
```

This is registered as a tool itself, so the agent can invoke it during a turn. The FAST chat path never calls this. The AGENT path uses it when the initial tool prompt lists tools compactly.

---

## Tool Result Format

All tools return JSON strings:

**Success:**
```json
{
    "status": "success",
    "tool": "read_file",
    "result": {"content": "...", "lines": 42}
}
```

**Error:**
```json
{
    "status": "error",
    "tool": "read_file",
    "message": "File not found: /path/to/file"
}
```

The agent loop treats `status: error` as an observation requiring a decision: retry with different args, try a different tool, or DONE with a failure message.

---

## Tool Caching

Read-only tools are cached per turn in `core/tool_cache.py`:

```python
_READ_ONLY_CACHEABLE = frozenset({
    "web_search", "search_google", "inspect_url",
    "read_file", "list_directory", "get_page_text",
    "get_current_url", "get_page_title",
})
```

The cache is keyed by `(tool_name, args_json)` and is invalidated at the start of each new turn. This prevents the agent from making duplicate network requests or file reads within a single task.

---

## MCP (Model Context Protocol) — Decision

**Current decision: Do not implement MCP yet.**

### Reasoning

MCP is a protocol standard for exposing tools to LLMs via JSON-RPC. It is useful when:
1. Tools need to be shared across multiple AI systems
2. Tools are maintained by third parties
3. The tool surface is large enough to justify a separate process

Immortality's current tool architecture:
- All tools are implemented as Python functions
- All tools run in-process (no latency overhead)
- All tools go through the same permission layer
- All tools are registered in one place

Migrating to MCP would add:
- Subprocess management per tool server
- JSON-RPC serialization overhead
- More complex error handling
- Process lifecycle management

This complexity is not justified by current requirements.

### When MCP Becomes Appropriate (Phase 6+)
- Integrating external services that already expose MCP servers (GitHub, Notion, Google Calendar)
- Building tools that other AI systems should also use
- When the tool count grows beyond ~100 and process isolation becomes desirable

### MCP Design (When Implemented)
```
Immortality
    ├── Internal tools (existing registry) — most tools
    └── MCP client
            ├── GitHub MCP server
            ├── Google Calendar MCP server
            ├── Notion MCP server
            └── Custom service servers
```

The internal `tool_registry.py` remains the primary registry. MCP tools are registered as wrappers that delegate to the MCP client.

---

## Tool Lifecycle

### Registration
Tools are registered in `tool_registry.py::setup()`. Called once at startup. Safe to call multiple times (guarded by `_setup_done` flag).

### Execution
Always via `registry.execute(tool_name, args)`. Never by calling tool functions directly.

### Deprecation
To deprecate a tool:
1. Add a deprecation note to the description
2. Remove from the prompt so the agent stops proposing it
3. Keep the function for backward compatibility during transition
4. Delete after confirming no pending actions reference it

### New Tools
New tools are added by:
1. Implementing the function in the appropriate `tools/*.py` file
2. Registering it in `tool_registry.py::setup()`
3. Adding permission tags
4. Writing a test that calls `registry.execute(tool_name, valid_args)`

---

## Future Tool Additions (Planned)

### Phase 2 — Computer Control
- `screenshot` — capture screen/region as PNG
- `list_windows` — enumerate open windows
- `find_window` — locate window by title
- `find_element` — find UI element by role/name
- `click_element` — click via accessibility API
- `type_text` — type into element
- `press_key` — keyboard shortcut
- `launch_app` — start application
- `close_window` — close window
- `get_clipboard` / `set_clipboard`

### Phase 3 — Screen Understanding
- `screen_analyze` — screenshot + vision model query
- `screen_read_text` — OCR from screen
- `screen_find_element` — vision-based element location

### Phase 7 — Proactive Integration (MCP)
- `calendar_events` — read calendar events
- `email_unread` — read unread email subjects
- `notification_send` — send desktop notification
