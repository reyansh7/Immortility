import inspect
import json
from typing import Callable

from tools.browser_agent import run_browser_goal
from tools.browser_tool import BrowserTool
from tools.web_search_tool import SearchTool
from tools.scraper_tool import ScraperTool
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

    def register(self, name: str, func: Callable, description: str, args_schema: dict) -> None:
        self._tools[name] = {
            "func": func,
            "description": description,
            "args_schema": args_schema,
        }

    def get_tool_prompt(self) -> str:
        prompt = "Available tools:\n\n"
        for name, info in self._tools.items():
            args_list = ", ".join(info["args_schema"].keys())
            prompt += f"- {name}({args_list}): {info['description']}\n"
        return prompt

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
        self.register("run_command", CommandTool.run_command, "Run a shell command", {"cmd": "string", "timeout": "float (optional)"})
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

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None


def setup_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.setup()
    return registry
