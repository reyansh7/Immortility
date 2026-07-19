import asyncio
import re

from rich.console import Console
from ollama import chat

from core.agent_state import AgentState
from core.action_engine import agent_step
from core.paths import get_desktop_path
from core.pending_action import format_confirmation_message
from agents.research_agent import ResearchAgent
from tools.leetcode_tool import fetch_solution_from_web, _extract_python_code, format_problem_for_llm
from tools.tool_registry import ToolRegistry

console = Console()


class LeetCodeSkill:
    """
    Dedicated LeetCode workflow — does not rely on Qwen to figure out steps.

    Research → Extract problem → Planner → Coder → Pending confirmation → Save
    """

    def __init__(self):
        self.state = AgentState()
        self.registry = ToolRegistry()
        self.registry.setup()

    async def run(self, user_input: str) -> str:
        console.print("\n[bold cyan]=== LeetCode Workflow ===[/bold cyan]")

        self.state.current_task = {
            "goal": user_input,
            "workflow": "leetcode",
            "step": "Researching",
        }
        self.state.save()

        researcher = ResearchAgent()
        ctx = await researcher.execute(user_input)
        problem = (self.state.current_task or {}).get("leetcode_problem")

        if not problem:
            diag = ctx.extracted_text or ctx.summary
            return f"LeetCode research failed.\nDiagnostics:\n{diag}"

        console.print(f"\n[bold]Problem:[/bold] #{problem['id']} — {problem['title']}")

        self.state.current_task["step"] = "Planning"
        self.state.save()
        console.print("\n[bold cyan]--- Planner ---[/bold cyan]")
        plan = await agent_step(
            "Planner",
            f"Create a concise algorithm plan for this LeetCode problem:\n{ctx.to_prompt()}",
        )
        console.print(plan)

        self.state.current_task["step"] = "Coding"
        self.state.save()
        console.print("\n[bold cyan]--- Coder ---[/bold cyan]")

        code = await self._generate_solution(problem, plan, ctx.to_prompt())
        console.print(f"[dim]Generated {len(code)} chars of code[/dim]")

        desktop = get_desktop_path()
        safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", problem["title"].lower())
        filename = f"leetcode_{problem['id']}_{safe_title}.py"
        file_path = str(desktop / filename)

        header = (
            f"# LeetCode #{problem['id']} - {problem['title']}\n"
            f"# Difficulty: {problem['difficulty']}\n"
            f"# URL: {problem['url']}\n"
            f"# Topics: {', '.join(problem.get('tags', []))}\n\n"
        )
        final_content = header + code

        self.state.current_task["step"] = "AwaitingConfirmation"
        self.state.pending_action = {
            "tool": "create_file",
            "args": {"path": file_path, "content": final_content},
            "workflow": "leetcode",
        }
        self.state.save()

        msg = format_confirmation_message("create_file", {"path": file_path})
        console.print(f"\n[bold yellow]{msg}[/bold yellow]")
        console.print("[dim]Solution preview:[/dim]")
        console.print(f"[green]{final_content[:600]}...[/green]")
        return msg

    async def _generate_solution(self, problem: dict, plan: str, research: str) -> str:
        web_code = None
        try:
            web_code = await fetch_solution_from_web(problem)
        except Exception as e:
            console.print(f"[yellow]Web solution fetch failed: {e}[/yellow]")

        if web_code:
            console.print("[green]Using verified web solution.[/green]")
            return web_code

        console.print("[cyan]Generating solution with Qwen...[/cyan]")
        prompt = (
            f"Write a complete Python LeetCode solution.\n\n"
            f"Plan:\n{plan}\n\nResearch:\n{research}\n\n"
            f"Use class Solution with the starter signature from the problem.\n"
            f"Output ONLY Python code, no markdown."
        )

        response = await asyncio.to_thread(
            chat,
            model="qwen3:8b",
            messages=[
                {
                    "role": "system",
                    "content": "Output only raw Python code. No explanations.",
                },
                {"role": "user", "content": prompt},
            ],
            think=True,
        )
        raw = response["message"]["content"].strip()
        extracted = _extract_python_code(raw)
        return extracted or raw
