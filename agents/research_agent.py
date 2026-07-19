import json
import re

from rich.console import Console

from core.agent_state import AgentState
from core.research_context import ResearchContext
from core.action_engine import agent_step
from core.paths import get_desktop_path
from core.pending_action import format_confirmation_message
from tools.tool_registry import ToolRegistry
from tools.leetcode_tool import (
    get_daily_challenge,
    format_problem_for_llm,
    fetch_solution_from_web,
    _extract_python_code,
    parse_problem_sections,
)

console = Console()


class ResearchAgent:
    """
    Gathers information via tools. Does NOT generate final answers or solutions.
    All tool calls go through ToolRegistry.
    """

    def __init__(self):
        self.state = AgentState()
        self.registry = ToolRegistry()
        self.registry.setup()

    async def execute(self, query: str) -> ResearchContext:
        console.print(f"\n[bold magenta]--- Research Agent ---[/bold magenta]")
        console.print(f"[cyan]Query:[/cyan] {query}")

        if "leetcode" in query.lower():
            return await self._research_leetcode(query)

        return await self._research_generic(query)

    async def _run_tool(self, tool: str, args: dict | None = None) -> dict:
        raw = await self.registry.execute(tool, args or {})
        return json.loads(raw)

    async def _research_leetcode(self, query: str) -> ResearchContext:
        diagnostics: list[str] = []
        problem = None

        console.print("[cyan]Fetching LeetCode daily challenge via API...[/cyan]")
        try:
            problem = await get_daily_challenge()
            console.print(
                f"[green]Found:[/green] #{problem['id']} — {problem['title']} ({problem['difficulty']})"
            )
        except Exception as e:
            diagnostics.append(f"GraphQL API failed: {e}")
            console.print(f"[yellow]API failed ({e}), trying alternatives...[/yellow]")

        if problem is None:
            problem = await self._leetcode_alternative_fetch(diagnostics)

        if problem is None:
            ctx = ResearchContext(
                query=query,
                sources=["FAILED"],
                extracted_text="\n".join(diagnostics),
                summary="Could not retrieve today's LeetCode problem. " + "; ".join(diagnostics),
            )
            self.state.set_research_context(ctx)
            return ctx

        sections = parse_problem_sections(problem)
        formatted = format_problem_for_llm(problem)

        ctx = ResearchContext(
            query=query,
            urls=[problem["url"]],
            sources=["LeetCode GraphQL API" if not diagnostics else "LeetCode API + fallback"],
            extracted_text=formatted,
            summary=(
                f"LeetCode #{problem['id']}: {problem['title']} ({problem['difficulty']}). "
                f"Title: {sections['title']}. "
                f"Constraints extracted: {bool(sections['constraints'])}."
            ),
        )

        self.state.current_task = {
            "goal": query,
            "workflow": "leetcode",
            "step": "Researching",
            "leetcode_problem": problem,
            "leetcode_sections": sections,
        }
        self.state.set_research_context(ctx)
        return ctx

    async def _leetcode_alternative_fetch(self, diagnostics: list[str]) -> dict | None:
        search = await self._run_tool(
            "search_google",
            {"query": "LeetCode Daily Challenge today site:leetcode.com"},
        )
        results = search.get("result", {}).get("results", [])

        if not results:
            search = await self._run_tool("search_google", {"query": "LeetCode Daily Challenge today"})
            results = search.get("result", {}).get("results", [])
            diagnostics.append("Used broader search query")

        leetcode_url = None
        for r in results:
            url = r.get("url", "")
            if "leetcode.com/problems/" in url:
                leetcode_url = url
                break

        if leetcode_url:
            console.print(f"[cyan]Scraping:[/cyan] {leetcode_url}")
            await self._run_tool("open_url", {"url": leetcode_url})
            text_result = await self._run_tool("get_page_text", {})
            text = text_result.get("result", {}).get("text", "")
            if len(text) > 500:
                diagnostics.append(f"Scraped {leetcode_url}")
                return {
                    "title": "Daily Challenge (scraped)",
                    "id": "unknown",
                    "difficulty": "Unknown",
                    "url": leetcode_url,
                    "description": text[:6000],
                    "examples": "",
                    "tags": [],
                    "hints": [],
                    "snippet": "",
                }

        diagnostics.append(f"Search returned {len(results)} results, no usable problem page")
        return None

    async def _research_generic(self, query: str) -> ResearchContext:
        search = await self._run_tool("search_google", {"query": query})
        results = search.get("result", {}).get("results", [])

        if not results:
            ctx = ResearchContext(
                query=query,
                sources=["Web Search"],
                summary=f"No results for: {query}",
            )
            self.state.set_research_context(ctx)
            return ctx

        top_url = results[0].get("url", "")
        console.print(f"[cyan]Browsing:[/cyan] {top_url}")
        await self._run_tool("open_url", {"url": top_url})
        text_result = await self._run_tool("get_page_text", {})
        text = text_result.get("result", {}).get("text", "")

        sources = [r.get("url", "") for r in results[:5] if r.get("url")]
        ctx = ResearchContext(
            query=query,
            urls=[top_url] if top_url else [],
            sources=sources or ["Web Search"],
            extracted_text=text[:8000] if text else "\n".join(
                f"{r.get('title')}: {r.get('snippet')}" for r in results[:5]
            ),
            summary=f"Research gathered from {len(results)} search results.",
        )
        self.state.set_research_context(ctx)
        console.print(f"[green]Research complete.[/green] {len(ctx.extracted_text)} chars collected.")
        return ctx
