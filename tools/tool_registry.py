import inspect
import json
from typing import Callable

from tools.browser_agent import run_browser_goal
from tools.browser_tool import BrowserTool
from tools.web_search_tool import SearchTool
from tools.scraper_tool import ScraperTool
from tools.link_inspect import inspect_url_tool
from tools.file_tool import FileTool
from tools.app_tool import AppTool
from tools.command_tool import CommandTool
from tools.system_tool import SystemTool


class ToolRegistry:
    """Central registry for all agent tools. Agents must use this to execute tools."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools = {}
            cls._instance._setup_done = False
        return cls._instance

    def register(
        self,
        name: str,
        func: Callable,
        description: str,
        args_schema: dict,
        permissions: dict | None = None,
        tags: list[str] | None = None,
    ) -> None:
        from core.capabilities import permissions_for_tool

        perms = permissions or permissions_for_tool(name)
        self._tools[name] = {
            "func": func,
            "description": description,
            "args_schema": args_schema,
            "permissions": perms,
            "tags": list(tags or []),
        }

    def get_tool_prompt(self, query: str | None = None) -> str:
        """Catalog for the agent prompt.

        FAST path never calls this. AGENT gets name(args)+one-line descriptions.
        Use discover_tools for a filtered subset with full schemas.
        """
        tools = self._tools
        if query:
            names = {row["name"] for row in self.discover(query)}
            tools = {k: v for k, v in self._tools.items() if k in names}
        prompt = (
            "Available tools (call discover_tools with a query like 'git' or 'pdf' "
            "to load a subset with full schemas):\n\n"
        )
        for name, info in tools.items():
            args_list = ", ".join(info["args_schema"].keys())
            perms = info.get("permissions") or {}
            tags = []
            if perms.get("read"):
                tags.append("read")
            if perms.get("write"):
                tags.append("write")
            if perms.get("network"):
                tags.append("network")
            if perms.get("destructive"):
                tags.append("destructive")
            extra = info.get("tags") or []
            tags.extend(t for t in extra if t not in tags)
            tag_s = f" [{', '.join(tags)}]" if tags else ""
            prompt += f"- {name}({args_list}){tag_s}: {info['description']}\n"
        return prompt

    def discover(self, query: str = "", tags: list[str] | None = None) -> list[dict]:
        """Find primitives by name/description/tag. No extra model call."""
        q = (query or "").strip().lower()
        words = [w for w in q.replace(",", " ").split() if w]
        wanted = {t.lower() for t in (tags or []) if t}
        out = []
        for name, info in self._tools.items():
            tool_tags = [str(t).lower() for t in (info.get("tags") or [])]
            blob = " ".join([name, info.get("description") or "", " ".join(tool_tags)]).lower()
            if wanted and not wanted.intersection(tool_tags):
                continue
            if words and not all(word in blob for word in words):
                continue
            out.append(
                {
                    "name": name,
                    "description": info["description"],
                    "args_schema": info["args_schema"],
                    "permissions": info.get("permissions") or {},
                    "tags": info.get("tags") or [],
                }
            )
        return out

    def get_permissions(self, name: str) -> dict:
        info = self._tools.get(name) or {}
        perms = info.get("permissions")
        if perms:
            return dict(perms)
        from core.capabilities import permissions_for_tool

        return permissions_for_tool(name)

    def list_tools(self) -> list[str]:
        return list(self._tools.keys())

    async def execute(self, tool_name: str, args: dict | None = None) -> str:
        args = args or {}
        if tool_name not in self._tools:
            return json.dumps({"status": "error", "message": f"Tool '{tool_name}' not found."})

        try:
            from editing.patch_args import normalize_patch_args

            if tool_name in {
                "edit_file", "insert_before", "insert_after", "replace_lines",
                "create_file", "write_file", "append_file", "replace_regex",
                "delete_block", "rename_symbol", "apply_patch",
            }:
                tool_name, args = normalize_patch_args(tool_name, args)

            if tool_name not in self._tools:
                return json.dumps({"status": "error", "message": f"Tool '{tool_name}' not found."})

            func = self._tools[tool_name]["func"]
            if inspect.iscoroutinefunction(func):
                result = await func(**args)
            else:
                result = func(**args)

            if isinstance(result, dict) and result.get("status") == "error":
                return json.dumps({"status": "error", "tool": tool_name, "result": result})

            return json.dumps({"status": "success", "tool": tool_name, "result": result})
        except TypeError as e:
            return json.dumps({"status": "error", "tool": tool_name, "message": f"Invalid args: {e}"})
        except Exception as e:
            return json.dumps({"status": "error", "tool": tool_name, "message": str(e)})

    def setup(self) -> None:
        """Register all required tools. Safe to call multiple times."""
        if self._setup_done:
            return
        self._setup_done = True

        self.register("open_url", BrowserTool.open_url, "Navigate browser to a URL", {"url": "string"})
        self.register(
            "inspect_url",
            inspect_url_tool,
            "Fetch a URL (GitHub repo, YouTube, any webpage) and return real page text. Use this instead of inventing.",
            {"url": "string"},
        )
        self.register(
            "search_google",
            SearchTool.search_google,
            "Search the live web (Tavily/Brave/SearXNG/DDG)",
            {"query": "string"},
        )
        self.register(
            "web_search",
            SearchTool.web_search,
            "Search the live web and return titled results with URLs",
            {"query": "string"},
        )
        self.register("scrape_page", ScraperTool.scrape_page, "Scrape HTML from URL or current page", {"url": "string (optional)"})
        self.register("get_current_url", BrowserTool.get_current_url, "Get current page URL", {})
        self.register("get_page_text", ScraperTool.get_page_text, "Extract readable text from current page", {})
        self.register("create_file", FileTool.create_file, "Create a NEW file only (fails if exists)", {"path": "string", "content": "string"})
        self.register("read_file", FileTool.read_file, "Read file — content is raw text; numbered has line numbers for replace_lines", {"path": "string"})
        self.register("write_file", FileTool.write_file, "Write content to a file", {"path": "string", "content": "string"})
        self.register("list_directory", FileTool.list_directory, "List directory contents", {"path": "string"})
        self.register(
            "run_command",
            CommandTool.run_command,
            "Run a shell command through the single execution engine (timeout/cwd/limits/cancel/trace).",
            {
                "cmd": "string",
                "timeout": "float (optional)",
                "cwd": "string (optional)",
                "max_output": "integer (optional)",
                "background": "bool (optional)",
            },
            tags=["terminal"],
        )
        self.register("open_application", AppTool.open_application, "Open a local application", {"name": "string", "path": "string (optional)"})
        self.register("get_system_status", SystemTool.get_system_status, "Get overall system CPU, memory, and disk usage", {})
        self.register("list_top_processes", SystemTool.list_top_processes, "List top system processes", {"sort_by": "string (memory|cpu)", "limit": "integer"})
        self.register("kill_process", SystemTool.kill_process, "Forcibly terminate a running process by PID", {"pid": "integer"})

        # Extended browser tools
        self.register("get_page_title", BrowserTool.get_page_title, "Get current page title", {})
        self.register("list_tabs", BrowserTool.list_tabs, "List open browser tabs", {})
        self.register("switch_tab", BrowserTool.switch_tab, "Switch to tab by index", {"index": "integer"})
        self.register("take_screenshot", BrowserTool.take_screenshot, "Save page screenshot", {"path": "string"})
        self.register(
            "browser_goal",
            run_browser_goal,
            "Run a browser goal via Planner→Executor→Validator (accessibility roles)",
            {"goal": "string", "start_url": "string (optional)"},
        )
        self.register("delete_file", FileTool.delete_file, "Delete a file", {"path": "string"})

        # Editing Phase 1.5 tools
        self.register("replace_lines", FileTool.replace_lines, "Replace a block of lines", {"path": "string", "start_line": "integer", "end_line": "integer", "replacement": "string"})
        self.register("edit_file", FileTool.edit_file, "Edit file using search and replace", {"path": "string", "target_text": "string", "replacement_text": "string"})
        self.register("append_file", FileTool.append_file, "Append text to a file", {"path": "string", "content": "string"})
        self.register("insert_before", FileTool.insert_before, "Insert content before target text", {"path": "string", "target_text": "string", "content": "string"})
        self.register("insert_after", FileTool.insert_after, "Insert content after target text", {"path": "string", "target_text": "string", "content": "string"})
        self.register("replace_regex", FileTool.replace_regex, "Replace regex pattern in file", {"path": "string", "pattern": "string", "replacement": "string"})
        self.register("delete_block", FileTool.delete_block, "Delete a block of text in a file", {"path": "string", "target_text": "string"})
        self.register("rename_symbol", FileTool.rename_symbol, "Rename a symbol in a file", {"path": "string", "old_name": "string", "new_name": "string"})
        self.register("find_symbol", FileTool.find_symbol, "Find a symbol in project", {"project": "string", "symbol": "string"})
        self.register("query_code_graph", FileTool.query_code_graph, "Query structural graph for dependencies and callers", {"project": "string", "query": "string"})
        self.register("find_references", FileTool.find_references, "Find references to a symbol", {"project": "string", "symbol": "string"})
        self.register("generate_diff", FileTool.generate_diff, "Generate a unified diff", {"old_text": "string", "new_text": "string"})
        self.register("apply_patch", FileTool.apply_patch, "Apply a patch to a file", {"path": "string", "patch": "string"})
        self.register("validate_patch", FileTool.validate_patch, "Validate a patch syntax", {"path": "string"})

        from tools.git_tool import GitTool
        from tools.docker_tool import DockerTool
        from tools.document_tool import extract_document
        from tools.database_tool import db_execute, db_mongo_find, db_query, list_connections

        self.register(
            "discover_tools",
            discover_tools,
            "Find tool primitives by query/tag (git, pdf, docker, database). Prefer this over guessing tool names.",
            {"query": "string", "tags": "string (optional, comma-separated)"},
            tags=["meta", "discovery"],
        )
        self.register(
            "git_status",
            GitTool.status,
            "Read-only git status (structured). cwd must be inside an allowed repo.",
            {"cwd": "string (optional)"},
            tags=["git", "readonly"],
        )
        self.register(
            "git_diff",
            GitTool.diff,
            "Read-only git diff. Optional staged=true, path, commit.",
            {"cwd": "string (optional)", "staged": "bool (optional)", "path": "string (optional)", "commit": "string (optional)"},
            tags=["git", "readonly"],
        )
        self.register(
            "git_log",
            GitTool.log,
            "Read-only git log as structured commits.",
            {"cwd": "string (optional)", "max_count": "integer (optional)", "path": "string (optional)"},
            tags=["git", "readonly"],
        )
        self.register(
            "git_show",
            GitTool.show,
            "Read-only git show for a revision.",
            {"rev": "string (optional)", "cwd": "string (optional)"},
            tags=["git", "readonly"],
        )
        self.register(
            "git_branch",
            GitTool.branch,
            "List branches (read-only). delete=name is destructive and requires confirmation.",
            {"cwd": "string (optional)", "all": "bool (optional)", "delete": "string (optional)", "force": "bool (optional)"},
            tags=["git"],
        )
        self.register(
            "git_remote",
            GitTool.remote,
            "List git remotes (read-only).",
            {"cwd": "string (optional)"},
            tags=["git", "readonly"],
        )
        self.register(
            "git_checkout",
            GitTool.checkout,
            "Switch branch/revision (mutating, confirmed). create=true makes a new branch.",
            {"target": "string", "cwd": "string (optional)", "create": "bool (optional)"},
            tags=["git", "mutating"],
        )
        self.register(
            "git_switch",
            GitTool.switch,
            "Same as git_checkout — switch or create a branch (confirmed).",
            {"target": "string", "cwd": "string (optional)", "create": "bool (optional)"},
            tags=["git", "mutating"],
        )
        self.register(
            "git_add",
            GitTool.add,
            "Stage paths inside the bounded repo (confirmed).",
            {"paths": "string or list", "cwd": "string (optional)", "all": "bool (optional)"},
            tags=["git", "mutating"],
        )
        self.register(
            "git_commit",
            GitTool.commit,
            "Create a commit (confirmed). amend rewrites HEAD and is destructive.",
            {"message": "string", "cwd": "string (optional)", "amend": "bool (optional)"},
            tags=["git", "mutating"],
        )
        self.register(
            "git_stash",
            GitTool.stash,
            "stash list is read-only; push/pop/apply are confirmed; drop/clear are destructive.",
            {"action": "string (list|push|pop|apply|drop|clear)", "cwd": "string (optional)", "message": "string (optional)"},
            tags=["git"],
        )
        self.register(
            "git_fetch",
            GitTool.fetch,
            "Fetch from a remote (confirmed, updates refs).",
            {"cwd": "string (optional)", "remote": "string (optional)"},
            tags=["git", "mutating"],
        )
        self.register(
            "git_push",
            GitTool.push,
            "Push to a remote (always confirmed). force=true is destructive and never silent.",
            {"cwd": "string (optional)", "remote": "string (optional)", "branch": "string (optional)", "force": "bool (optional)"},
            tags=["git", "mutating"],
        )
        self.register(
            "git_reset",
            GitTool.reset,
            "git reset (confirmed). mode=hard is destructive and never silent.",
            {"mode": "string (soft|mixed|hard)", "target": "string (optional)", "cwd": "string (optional)"},
            tags=["git", "mutating"],
        )
        self.register(
            "extract_document",
            extract_document,
            "Parse PDF/DOC/DOCX/PPTX/XLSX/CSV with real parsers and return structured text/tables.",
            {"path": "string", "max_chars": "integer (optional)"},
            tags=["documents", "pdf", "doc", "docx", "pptx", "xlsx", "csv", "readonly"],
        )
        self.register(
            "docker_ps",
            DockerTool.ps,
            "List Docker containers (read-only).",
            {"all": "bool (optional)"},
            tags=["docker", "readonly"],
        )
        self.register(
            "docker_images",
            DockerTool.images,
            "List Docker images (read-only).",
            {},
            tags=["docker", "readonly"],
        )
        self.register(
            "docker_inspect",
            DockerTool.inspect,
            "Inspect a Docker container or image (read-only).",
            {"target": "string"},
            tags=["docker", "readonly"],
        )
        self.register(
            "docker_logs",
            DockerTool.logs,
            "Read recent Docker container logs (read-only).",
            {"container": "string", "tail": "integer (optional)"},
            tags=["docker", "readonly"],
        )
        self.register(
            "docker_info",
            DockerTool.info,
            "Docker daemon info/status (read-only).",
            {},
            tags=["docker", "readonly"],
        )
        self.register(
            "docker_version",
            DockerTool.version,
            "Docker client/server version (read-only).",
            {},
            tags=["docker", "readonly"],
        )
        self.register(
            "docker_stop",
            DockerTool.stop,
            "Stop a container (confirmed).",
            {"container": "string"},
            tags=["docker", "mutating"],
        )
        self.register(
            "docker_rm",
            DockerTool.rm,
            "Remove a container (destructive, never silent).",
            {"target": "string", "force": "bool (optional)"},
            tags=["docker", "destructive"],
        )
        self.register(
            "db_list_connections",
            list_connections,
            "List named configured database connections (no secrets, no arbitrary URLs).",
            {},
            tags=["database", "readonly"],
        )
        self.register(
            "db_query",
            db_query,
            "Read-only SQL (SELECT/WITH) against a named configured SQLite or Postgres connection.",
            {"connection": "string", "sql": "string", "max_rows": "integer (optional)"},
            tags=["database", "sqlite", "postgres", "readonly"],
        )
        self.register(
            "db_execute",
            db_execute,
            "Write SQL on a connection that allows write (confirmed). DROP/TRUNCATE/ALTER are destructive.",
            {"connection": "string", "sql": "string"},
            tags=["database", "mutating"],
        )
        self.register(
            "db_mongo_find",
            db_mongo_find,
            "Find documents on a named configured MongoDB connection (read-only).",
            {
                "connection": "string",
                "collection": "string",
                "filter_json": "string (optional)",
                "database": "string (optional)",
                "limit": "integer (optional)",
            },
            tags=["database", "mongo", "readonly"],
        )

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None


def discover_tools(query: str = "", tags: str = "") -> dict:
    registry = ToolRegistry()
    registry.setup()
    tag_list = [t.strip() for t in str(tags or "").split(",") if t.strip()]
    matches = registry.discover(query or "", tags=tag_list or None)
    return {
        "status": "success",
        "query": query,
        "tags": tag_list,
        "tools": matches,
        "count": len(matches),
    }


def setup_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.setup()
    return registry
