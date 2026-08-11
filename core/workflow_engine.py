"""Phase 3 autonomous workflow orchestrator."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from rich.console import Console

from core.action_engine import agent_step
from core.checkpoint_manager import CheckpointManager
from core.decision_engine import DecisionEngine
from core.event_bus import EventBus
from core.verifier import Verifier
from core.workflow_executor import WorkflowExecutor
from core.workflow_history import WorkflowHistory
from core.workflow_scheduler import WorkflowScheduler
from core.workflow_state import WorkflowState, WorkflowStatus, WorkflowStep
from core.workflow_validator import WorkflowValidator
from editing.edit_planner import EditPlanner

logger = logging.getLogger(__name__)
console = Console()


class WorkflowEngine:
    """Orchestrates autonomous software engineering workflows."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            try:
                from core.repo_paths import workflow_db_path

                db_path = str(workflow_db_path())
            except Exception:
                db_path = "workflow.db"
        self.state = WorkflowState(db_path=db_path)
        self.history = WorkflowHistory(db_path=db_path)
        self.scheduler = WorkflowScheduler(self.state)
        self.executor = WorkflowExecutor(self.state, self.history)
        self.validator = WorkflowValidator()
        self.checkpoints = CheckpointManager()
        self.events = EventBus()

    def start_workflow(self, context: dict[str, Any]) -> str:
        ok, msg = self.validator.validate_context(context)
        if not ok:
            raise ValueError(msg)
        workflow_id = str(uuid.uuid4())
        self.state.create_workflow(workflow_id, WorkflowStep.PLANNING, context)
        self.history.log_event(workflow_id, "WORKFLOW_CREATED", WorkflowStep.PLANNING)
        self.events.publish("workflow.created", {"workflow_id": workflow_id})
        return workflow_id

    def pause_workflow(self, workflow_id: str) -> None:
        self.scheduler.pause(workflow_id)
        self.history.log_event(workflow_id, "WORKFLOW_PAUSED")

    def resume_workflow(self, workflow_id: str) -> None:
        self.scheduler.resume(workflow_id)
        self.history.log_event(workflow_id, "WORKFLOW_RESUMED")

    async def resume_from_checkpoint(self, workflow_id: str) -> dict[str, Any]:
        """Rollback to last checkpoint and re-run workflow from current step."""
        workflow = self.state.get_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Unknown workflow: {workflow_id}")
        context = workflow.get("context", {})
        ckpt = context.get("last_checkpoint")
        if ckpt:
            restored = self.checkpoints.rollback(ckpt)
            if restored:
                console.print(
                    f"[yellow]Restored {len(restored)} file(s) from checkpoint[/yellow]"
                )
        self.state.update_workflow(workflow_id, status=WorkflowStatus.RUNNING, paused=False)
        self.history.log_event(workflow_id, "WORKFLOW_RESUMED_FROM_CHECKPOINT")
        return await self.run_workflow(workflow_id)

    async def run_workflow(self, workflow_id: str) -> dict[str, Any]:
        self.state.update_workflow(workflow_id, status=WorkflowStatus.RUNNING)
        self.history.log_event(workflow_id, "WORKFLOW_STARTED")
        self.events.publish("workflow.started", {"workflow_id": workflow_id})

        while True:
            await self.scheduler.wait_while_paused(workflow_id)

            workflow = self.state.get_workflow(workflow_id)
            if not workflow:
                break
            if workflow["status"] in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
                WorkflowStatus.CANCELLED,
            ):
                break

            current_step: WorkflowStep = workflow["current_step"]
            context: dict[str, Any] = workflow.get("context", {})
            retry_count = int(context.get(f"{current_step.value}_retries", 0))

            console.print(f"\n[bold cyan]--- {current_step.value.upper()} ---[/bold cyan]")
            logger.info("Workflow %s step: %s", workflow_id, current_step.value)

            success = False
            try:
                success = await self._run_step(workflow_id, current_step, context)
            except Exception as exc:
                logger.exception("Step %s failed: %s", current_step.value, exc)
                console.print(f"[red]Error in {current_step.value}: {exc}[/red]")
                success = False

            if success:
                context[f"{current_step.value}_retries"] = 0
            else:
                context[f"{current_step.value}_retries"] = retry_count + 1

            self.state.update_workflow(workflow_id, context=context)

            next_step = DecisionEngine.get_next_step(
                current_step, success, retry_count=retry_count
            )

            if next_step is None:
                if success:
                    self.state.update_workflow(
                        workflow_id, status=WorkflowStatus.COMPLETED
                    )
                    self.history.log_event(workflow_id, "WORKFLOW_COMPLETED")
                    console.print("[bold green]Workflow completed successfully.[/bold green]")
                    self.events.publish("workflow.completed", {"workflow_id": workflow_id})
                else:
                    self.state.update_workflow(workflow_id, status=WorkflowStatus.FAILED)
                    self.history.log_event(workflow_id, "WORKFLOW_FAILED")
                    console.print("[bold red]Workflow failed (max retries).[/bold red]")
                    self.events.publish("workflow.failed", {"workflow_id": workflow_id})
                break

            self.state.update_workflow(workflow_id, current_step=next_step)
            self.history.log_event(workflow_id, "WORKFLOW_TRANSITION", next_step)

        final = self.state.get_workflow(workflow_id) or {}
        return {
            "workflow_id": workflow_id,
            "status": final.get("status", WorkflowStatus.FAILED).value
            if isinstance(final.get("status"), WorkflowStatus)
            else str(final.get("status", "unknown")),
            "context": final.get("context", {}),
        }

    async def _run_step(
        self, workflow_id: str, step: WorkflowStep, context: dict[str, Any]
    ) -> bool:
        goal = context.get("goal", "")
        proj_root = context.get("project_root", "")
        rag_context = context.get("rag_context", "")

        if step == WorkflowStep.PLANNING:
            reflection_block = ""
            try:
                from knowledge.engine import KnowledgeEngine

                ke = KnowledgeEngine()
                proj_name = Path(proj_root).name if proj_root else ke.get_active_project_name()
                refs = ke.list_reflections(project=proj_name or None, limit=8)
                from knowledge.reflection_format import format_reflections_block

                reflection_block = format_reflections_block(refs, goal=goal)
            except Exception as exc:
                logger.debug("reflection inject skipped: %s", exc)
            prompt = f"Create a step-by-step plan for: {goal}"
            if reflection_block:
                prompt += f"\n\n{reflection_block}"
            if rag_context:
                prompt += f"\n\nProject context:\n{rag_context[:6000]}"
            plan = await agent_step("Planner", prompt)
            context["plan"] = plan
            console.print(plan)
            return True

        if step == WorkflowStep.RESEARCH:
            needs_research = any(
                w in goal.lower()
                for w in ("research", "best practice", "how to", "compare", "investigate")
            )
            if needs_research or context.get("force_research"):
                from agents.research_agent import ResearchAgent

                researcher = ResearchAgent()
                research_ctx = await researcher.execute(goal)
                context["research"] = research_ctx.to_prompt()
                rag_context = (rag_context + "\n\n" + context["research"]).strip()
                context["rag_context"] = rag_context
                console.print("[dim]Research context attached to workflow[/dim]")
            return True

        if step == WorkflowStep.KNOWLEDGE_RETRIEVAL:
            if proj_root and not rag_context:
                try:
                    from knowledge.engine import KnowledgeEngine
                    engine = KnowledgeEngine()
                    engine.open_project(proj_root)
                    rag_context = engine.get_context(goal)
                    context["rag_context"] = rag_context
                    console.print(f"[dim]Retrieved {len(rag_context)} chars of context[/dim]")
                except Exception as exc:
                    logger.warning("Knowledge retrieval failed: %s", exc)
            return True

        if step == WorkflowStep.EDIT_PLANNER:
            if proj_root:
                reflection_block = ""
                try:
                    from knowledge.engine import KnowledgeEngine

                    ke = KnowledgeEngine()
                    refs = ke.list_reflections(project=Path(proj_root).name, limit=5)
                    from knowledge.reflection_format import format_reflections_block

                    reflection_block = format_reflections_block(refs, goal=goal)
                except Exception:
                    reflection_block = ""
                planner_context = rag_context
                if reflection_block:
                    planner_context = f"{reflection_block}\n\n{rag_context}"
                planner = EditPlanner()
                plan = await planner.generate_plan(goal, planner_context)
                context["edit_plan"] = {
                    "goal": plan.goal,
                    "files_to_read": plan.files_to_read,
                    "files_to_edit": plan.files_to_edit,
                    "plan_steps": plan.plan_steps,
                }
                console.print(f"[green]Edit plan:[/green] {plan.goal}")
            return True

        if step == WorkflowStep.PATCH_GENERATOR:
            if not proj_root:
                console.print("[yellow]No project root — skipping coding.[/yellow]")
                return True
            planner_hint = context.get("planner_hint") or context.get("plan", "")
            error_text = context.get("error_text", "")
            result = await self.executor.run_coding_workflow(
                workflow_id,
                goal,
                proj_root,
                rag_context,
                planner_hint=planner_hint,
                error_text=error_text,
            )
            context["coding_result"] = result
            context["files_modified"] = result.get("files_modified", [])
            if result.get("files_modified"):
                ckpt = self.checkpoints.save(
                    workflow_id, context, result["files_modified"]
                )
                context["last_checkpoint"] = ckpt
            if result.get("success"):
                console.print(f"[green]{result.get('message')}[/green]")
                return True
            console.print(f"[yellow]{result.get('message')}[/yellow]")
            return False

        if step == WorkflowStep.EXECUTOR:
            # CodingWorkflow already applied patches in PATCH_GENERATOR
            return bool(context.get("coding_result", {}).get("success"))

        if step == WorkflowStep.VERIFIER:
            files = context.get("files_modified", [])
            if proj_root and files:
                verifier = Verifier(proj_root)
                result = await verifier.verify_project(files)
                context["verification_results"] = result
                if result.get("success"):
                    console.print("[green]Verification passed (files + build + tests).[/green]")
                    return True
                console.print(f"[red]Verification failed: {result.get('details', '')[:500]}[/red]")
                if context.get("last_checkpoint"):
                    restored = self.checkpoints.rollback(context["last_checkpoint"])
                    if restored:
                        console.print(
                            f"[yellow]Rolled back {len(restored)} file(s) before debugger[/yellow]"
                        )
                return False
            if proj_root:
                verifier = Verifier(proj_root)
                result = await verifier.verify_project([])
                context["verification_results"] = result
                return result.get("success", True)
            return True

        if step == WorkflowStep.DEBUGGER:
            errors = context.get("verification_results", {})
            coding = context.get("coding_result", {})
            prompt = (
                f"Previous edits failed verification or coding.\n"
                f"Verification: {json.dumps(errors, indent=2)[:3000]}\n"
                f"Coding result: {json.dumps(coding, indent=2)[:2000]}\n"
                f"Goal: {goal}\n"
                f"Return JSON with files_to_read, files_to_edit, plan_steps to fix the issue."
            )
            fix_plan = await agent_step("Debugger", prompt)
            context["plan"] = fix_plan
            context["planner_hint"] = fix_plan
            if errors.get("stage") == "build" and errors.get("build_output"):
                context["error_text"] = errors["build_output"]
            console.print(fix_plan)
            return True

        if step == WorkflowStep.REFLECTOR:
            result = context.get("coding_result", {})
            verification = context.get("verification_results", {})
            reflection = await agent_step(
                "Reflector",
                f"Summarize what worked and what to improve.\n"
                f"Goal: {goal}\n"
                f"Coding: {json.dumps(result, indent=2)[:2000]}\n"
                f"Verification: {json.dumps(verification, indent=2)[:1000]}",
            )
            context["reflection"] = reflection
            console.print(f"[dim]{reflection[:400]}[/dim]")
            return True

        if step == WorkflowStep.EXPERIENCE_UPDATE:
            try:
                from memory.experience_memory import ExperienceMemory
                exp = ExperienceMemory()
                result = context.get("coding_result", {})
                verification = context.get("verification_results", {})
                success = bool(result.get("success"))
                reflection_text = str(context.get("reflection") or "")
                details = result.get("message", "")
                if reflection_text:
                    details = f"{details}\nReflection: {reflection_text[:800]}".strip()
                exp.record(
                    task=goal,
                    outcome="success" if success else "failure",
                    details=details,
                )
                # Phase 2: durable structured reflection in knowledge graph
                from knowledge.engine import KnowledgeEngine
                ke = KnowledgeEngine()
                broke = "" if success else str(verification.get("details", result.get("message", "")))[:2000]
                fixed = str(result.get("message", ""))[:2000] if success else ""
                if reflection_text:
                    if success:
                        fixed = f"{fixed}\n{reflection_text[:1500]}".strip()
                    else:
                        broke = f"{broke}\n{reflection_text[:1500]}".strip()
                ke.add_reflection(
                    task=goal,
                    what_broke=broke,
                    what_fixed_it=fixed,
                    files_modified=list(result.get("files_modified", []) or context.get("files_modified", [])),
                    project=Path(proj_root).name if proj_root else None,
                )
                try:
                    from core.self_reflection import reflect_and_store

                    reflect_and_store(
                        task=goal,
                        tools_used=["coding_workflow"],
                        result=str(result.get("message", "")),
                        error="" if success else str(verification.get("details", "")),
                        solution=str(result.get("message", "")) if success else "",
                        success=success,
                        project=Path(proj_root).name if proj_root else None,
                    )
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("Experience update skipped: %s", exc)
            return True

        return False
