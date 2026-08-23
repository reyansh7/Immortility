import json
import re

from dataclasses import dataclass, field
from typing import Any

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


@dataclass
class ResearchResult:
    """Minimal URL-preserving research outcome. Not a citation engine."""

    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"answer": self.answer, "sources": list(self.sources)}


class ResearchAgent:
    """
    Gathers information via tools. Does NOT generate final answers or solutions.
    All tool calls go through ToolRegistry.
    """

    def __init__(self):
        self.state = AgentState()
        self.registry = ToolRegistry()
        self.registry.setup()
        self.last_result: ResearchResult | None = None

    async def execute(self, query: str) -> ResearchContext:
        console.print(f"\n[bold magenta]--- Research Agent ---[/bold magenta]")
        console.print(f"[cyan]Query:[/cyan] {query}")

        if "leetcode" in query.lower():
            return await self._research_leetcode(query)

        from tools.link_inspect import extract_urls, get_last_url, inspect_url, wants_link_inspect

        urls = extract_urls(query)
        if not urls and wants_link_inspect(query):
            last = get_last_url()
            if last:
                urls = [last]
        if urls:
            return await self._research_urls(query, urls)

        return await self._research_generic(query)

    async def _research_urls(self, query: str, urls: list[str]) -> ResearchContext:
        """Fetch the pasted URL(s) first. Never invent from model memory."""
        from agents.research_synth import ResearchSource, synthesize_with_citations
        from tools.link_inspect import inspect_url

        sources: list[ResearchSource] = []
        texts: list[str] = []
        kept: list[str] = []
        for url in urls[:3]:
            console.print(f"[cyan]Inspecting URL:[/cyan] {url}")
            page = inspect_url(url)
            text = str(page.get("text") or "").strip()
            title = str(page.get("title") or url)
            if page.get("ok") and text:
                sources.append(
                    ResearchSource(title=title, url=url, snippet=text[:400], text=text)
                )
                texts.append(f"## {title}\nURL: {url}\n{text[:6000]}")
                kept.append(url)
            else:
                console.print(
                    f"[yellow]Fetch failed:[/yellow] {page.get('error') or 'empty'}"
                )

        llm_answer = ""
        if texts:
            try:
                from core.config import get_config
                from core.llm import fast_chat

                bundle = "\n\n".join(texts)[:12000]
                tokens = get_config().chat_max_tokens
                llm_answer = fast_chat(
                    query,
                    extra_context=(
                        "Answer ONLY from this fetched page content. "
                        "Do not invent. Cite the URL. Finish the answer; never stop mid-sentence.\n\n"
                        f"{bundle}"
                    ),
                    max_output_tokens=tokens,
                )
            except Exception:
                llm_answer = ""

        synth = synthesize_with_citations(query, sources, llm_answer=llm_answer)
        ctx = ResearchContext(
            query=query,
            urls=kept or urls[:3],
            sources=kept or urls[:3],
            extracted_text="\n\n".join(texts)[:10000] or synth.answer,
            summary=synth.answer,
        )
        self.state.set_research_context(ctx)
        self.last_result = ResearchResult(
            answer=synth.answer,
            sources=[{"url": s.url, "title": s.title} for s in sources if s.url],
        )
        return ctx

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
            search_state = (search.get("result") or {}).get("search_state") or search.get("search_state")
            note = ""
            if search_state == "SEARCH_EXECUTED_ZERO_RESULTS":
                note = " Search ran and found nothing."
            elif search_state == "SEARCH_UNAVAILABLE":
                note = " Search was unavailable."
            ctx = ResearchContext(
                query=query,
                sources=[],
                summary=(synth.answer + note).strip(),
                extracted_text=synth.answer,
            )
            self.state.set_research_context(ctx)
            self.last_result = ResearchResult(answer=ctx.summary, sources=[])
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
            from core.config import get_config
            from core.llm import fast_chat

            bundle = "\n\n".join(texts)[:7000]
            tokens = get_config().chat_max_tokens
            llm_answer = fast_chat(
                query,
                extra_context=(
                    "Synthesize an answer ONLY from these web sources. "
                    "Include source URLs inline or in a Sources list. "
                    "If sources are insufficient, say so — do not invent. "
                    "Finish the answer; never stop mid-sentence.\n\n"
                    f"{bundle}"
                ),
                max_output_tokens=tokens,
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
        self.last_result = ResearchResult(
            answer=answer,
            sources=[{"url": s.url, "title": s.title} for s in sources if s.url],
        )
        return ctx
