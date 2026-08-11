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
        from agents.research_synth import ResearchSource, synthesize_with_citations

        search = await self._run_tool("search_google", {"query": query})
        inner = search.get("result") or {}
        if isinstance(inner, dict) and "results" in inner:
            results = inner.get("results") or []
        else:
            results = search.get("results") or []

        if not results:
            # Optional refined second query
            refined = f"{query} overview documentation"
            search2 = await self._run_tool("search_google", {"query": refined})
            inner2 = search2.get("result") or {}
            results = (inner2.get("results") if isinstance(inner2, dict) else None) or []

        if not results:
            synth = synthesize_with_citations(query, [])
            ctx = ResearchContext(
                query=query,
                sources=[],
                summary=synth.answer,
                extracted_text=synth.answer,
            )
            self.state.set_research_context(ctx)
            return ctx

        sources: list[ResearchSource] = []
        urls: list[str] = []
        texts: list[str] = []
        max_pages = 3
        for r in results[:max_pages]:
            url = (r.get("url") or "").strip()
            title = (r.get("title") or "").strip()
            snippet = (r.get("snippet") or "").strip()
            page_text = ""
            if url:
                try:
                    console.print(f"[cyan]Reading:[/cyan] {url}")
                    await self._run_tool("open_url", {"url": url})
                    text_result = await self._run_tool("get_page_text", {})
                    page_text = str(
                        (text_result.get("result") or {}).get("text") or ""
                    )[:4000]
                except Exception as exc:
                    console.print(f"[yellow]Skip {url}: {exc}[/yellow]")
            sources.append(
                ResearchSource(title=title, url=url, snippet=snippet, text=page_text)
            )
            if url:
                urls.append(url)
            if page_text:
                texts.append(f"## {title}\nURL: {url}\n{page_text[:2500]}")
            elif snippet:
                texts.append(f"## {title}\nURL: {url}\n{snippet}")

        # Coverage check — refine once if thin
        combined_len = sum(len(t) for t in texts)
        if combined_len < 400 and results:
            alt_q = f"{query} explained site:docs OR documentation"
            search3 = await self._run_tool("search_google", {"query": alt_q})
            inner3 = search3.get("result") or {}
            extra = (inner3.get("results") if isinstance(inner3, dict) else None) or []
            for r in extra[:2]:
                url = (r.get("url") or "").strip()
                if not url or url in urls:
                    continue
                snippet = (r.get("snippet") or "").strip()
                sources.append(
                    ResearchSource(
                        title=r.get("title") or "",
                        url=url,
                        snippet=snippet,
                        text=snippet,
                    )
                )
                urls.append(url)
                if snippet:
                    texts.append(f"## {r.get('title')}\nURL: {url}\n{snippet}")

        llm_answer = ""
        try:
            from core.llm import fast_chat

            bundle = "\n\n".join(texts)[:7000]
            llm_answer = fast_chat(
                query,
                extra_context=(
                    "Synthesize an answer ONLY from these web sources. "
                    "Include source URLs inline or in a Sources list. "
                    "If sources are insufficient, say so — do not invent.\n\n"
                    f"{bundle}"
                ),
                max_output_tokens=400,
            )
        except Exception:
            llm_answer = ""

        synth = synthesize_with_citations(query, sources, llm_answer=llm_answer)
        answer = synth.answer
        try:
            from core.critic import apply_critic_or_retry

            answer = apply_critic_or_retry(
                query,
                answer,
                sources=synth.citations,
                mode="WEB",
            )
        except Exception:
            pass
        ctx = ResearchContext(
            query=query,
            urls=urls[:8],
            sources=urls[:8] or ["Web Search"],
            extracted_text="\n\n".join(texts)[:10000] or answer,
            summary=answer,
        )
        self.state.set_research_context(ctx)
        try:
            from knowledge.learner import remember_web_research

            remember_web_research(
                query,
                synth.answer[:6000],
                sources=list(ctx.sources or []),
            )
        except Exception:
            pass
        console.print(
            f"[green]Research complete.[/green] "
            f"{len(sources)} sources, {len(ctx.extracted_text)} chars."
        )
        return ctx
