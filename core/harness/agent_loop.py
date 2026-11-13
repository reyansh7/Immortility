"""Iterative agent loop for the IMMORTALITY Custom Harness.

Implements the true reasoning loop:
  Context -> Brain -> Action Proposal -> Permission Gate -> Tool Execution ->
  Observation -> Memory / Re-reasoning -> Verification -> Recovery -> Completion.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from core.agent_state import AgentState
from core.harness.contracts import BrainProvider, FailureClass
from core.harness.context_builder import ContextBuilder
from core.harness.recovery import RecoveryManager
from core.harness.tool_executor import ALL_MUTATING_TOOLS, HarnessToolExecutor
from core.harness.verifier import HarnessVerifier
from core.json_utils import parse_llm_json
from core.pending_action import format_confirmation_message
from core.task import Task, TaskStatus
from core.task_manager import TaskManager, get_task_manager

logger = logging.getLogger(__name__)


class HarnessAgentLoop:
    """Executes iterative, observation-driven reasoning cycles."""

    def __init__(
        self,
        task_manager: TaskManager | None = None,
        context_builder: ContextBuilder | None = None,
        tool_executor: HarnessToolExecutor | None = None,
        verifier: HarnessVerifier | None = None,
        recovery_manager: RecoveryManager | None = None,
    ) -> None:
        self.task_manager = task_manager or get_task_manager()
        self.context_builder = context_builder or ContextBuilder()
        self.tool_executor = tool_executor or HarnessToolExecutor()
        self.verifier = verifier or HarnessVerifier()
        self.recovery_manager = recovery_manager or RecoveryManager(task_manager=self.task_manager)

    async def run_loop(
        self,
        task: Task,
        brain: BrainProvider,
        *,
        max_steps: int = 15,
        require_edits: bool = False,
        auto_confirm: bool = False,
        context_override: str = "",
        user_context: str = "",
        world_context: str = "",
        memory_context: str = "",
        internal_history: list[dict[str, str]] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        skip_confirm_sigs: set[str] | None = None,
        persist: bool = True,
    ) -> str:
        """Run the iterative agent reasoning loop to completion or pause."""
        from core.execution_kernel import get_kernel

        kernel = get_kernel()
        history: list[dict[str, str]] = list(internal_history or [])
        skip_sigs = set(skip_confirm_sigs or ())
        tm = self.task_manager if persist else None

        # Ensure task is marked EXECUTING in persistent store
        if tm is not None:
            tm.store.update_status(task.id, TaskStatus.EXECUTING)

        evidence_notes: list[str] = []
        recovery_hints: list[str] = []
        done_reject_count = 0

        tools_catalog = self.tool_executor.registry.get_tool_prompt()

        for step in range(max_steps):
            if kernel.cancelled():
                if tm is not None:
                    tm.cancel_task(task.id)
                return "Task cancelled by user or kernel."

            current_step_idx = min(step, len(task.plan) - 1) if task.plan else 0
            if tm is not None and task.plan and 0 <= current_step_idx < len(task.plan):
                tm.begin_step(task.id, current_step_idx)

            # 1. Build turn context
            system_prompt = self.context_builder.build_system_prompt(
                task,
                tools_catalog=tools_catalog,
                user_context=user_context,
                world_context=world_context,
                memory_context=memory_context,
                recovery_hints=recovery_hints,
                require_edits=require_edits,
                context_override=context_override,
            )

            messages = [{"role": "system", "content": system_prompt}]
            if not history:
                messages.append({"role": "user", "content": f"Task: {task.goal}"})
            else:
                messages.extend(history)

            # 2. Reason via BrainProvider
            role = "code" if require_edits else "brain"
            try:
                response_text = await brain.generate_response(messages, role=role)
            except Exception as exc:
                err_msg = f"Brain reasoning failed: {exc}"
                logger.error(err_msg)
                if tm is not None:
                    tm.fail_task(task.id, err_msg)
                return err_msg

            history.append({"role": "assistant", "content": response_text})

            # 3. Parse action proposal
            try:
                data = parse_llm_json(response_text)
                tool_name = str(data.get("tool", "")).strip()
                args = data.get("args", {}) or {}
            except Exception as exc:
                rec = self.recovery_manager.evaluate(task, f"JSON parse error: {exc}", tool_name="json_parser")
                if tm is not None:
                    tm.record_error(
                        task.id,
                        step_index=current_step_idx,
                        error_type=FailureClass.MALFORMED_OUTPUT.value,
                        message=str(exc),
                        retry_number=rec.retry_count,
                    )
                recovery_hints.append(rec.hint)
                history.append({"role": "user", "content": f"Invalid JSON output. {rec.hint}"})
                continue

            # 4. Handle DONE tool
            if tool_name.upper() == "DONE":
                final_message = str(args.get("message", "Task completed."))

                # Verification gate for editing tasks
                if require_edits and not self.tool_executor.executed_tools.intersection(ALL_MUTATING_TOOLS):
                    done_reject_count += 1
                    if done_reject_count >= 2:
                        # Accept analysis fallback
                        if tm is not None:
                            tm.complete_task(task.id, final_message)
                        return final_message
                    err = "DONE rejected: No modifications were applied. Use write_file/edit_file or explain findings."
                    history.append({"role": "user", "content": err})
                    continue

                if self.tool_executor.executed_tools.intersection(ALL_MUTATING_TOOLS):
                    touched = list(self.tool_executor.before_snapshots.keys())
                    v_res = await self.verifier.verify(
                        task.goal,
                        touched,
                        self.tool_executor.before_snapshots,
                        brain=brain,
                    )
                    if tm is not None:
                        tm.record_verification(task.id, v_res)

                    if not v_res.passed:
                        rec = self.recovery_manager.evaluate(
                            task,
                            f"Verification failed: {v_res.details}",
                            tool_name="verifier",
                        )
                        if tm is not None:
                            tm.record_error(
                                task.id,
                                step_index=current_step_idx,
                                error_type=FailureClass.VERIFICATION_FAILED.value,
                                message=v_res.details,
                                retry_number=rec.retry_count,
                            )
                        if rec.stop_execution:
                            fail_msg = f"Task verification failed after retries: {v_res.details}"
                            if tm is not None:
                                tm.fail_task(task.id, fail_msg)
                            return fail_msg
                        recovery_hints.append(rec.hint)
                        history.append({
                            "role": "user",
                            "content": f"DONE rejected: Verification failed.\n{v_res.details}\n{rec.hint}",
                        })
                        continue

                # Verification succeeded
                if tm is not None and task.plan and 0 <= current_step_idx < len(task.plan):
                    tm.complete_step(task.id, current_step_idx)
                if tm is not None:
                    tm.complete_task(task.id, final_message)
                return final_message

            # 5. Validate schema
            valid, schema_err = self.tool_executor.validate_request(tool_name, args)
            if not valid:
                history.append({"role": "user", "content": f"Tool error: {schema_err}"})
                continue

            # 6. Policy & Permission Gating
            decision = self.tool_executor.evaluate_policy(
                tool_name,
                args,
                permission_mode=task.permission_mode,
                require_edits=require_edits,
            )

            call_sig = f"{tool_name}|{json.dumps(decision.normalized_args, sort_keys=True)}"

            if decision.denied:
                err_text = f"Blocked by permission policy: {decision.reason}"
                rec = self.recovery_manager.evaluate(task, err_text, tool_name=tool_name, tool_args=args)
                if tm is not None:
                    tm.record_error(
                        task.id,
                        step_index=current_step_idx,
                        error_type=FailureClass.PERMISSION_DENIED.value,
                        message=decision.reason,
                        retry_number=rec.retry_count,
                    )
                if rec.stop_execution:
                    if tm is not None:
                        tm.fail_task(task.id, err_text)
                    return err_text
                history.append({"role": "user", "content": f"{err_text}\n{rec.hint}"})
                continue

            if decision.requires_confirmation and not auto_confirm and call_sig not in skip_sigs:
                # Suspend task in WAITING_PERMISSION state
                confirm_prompt = format_confirmation_message(tool_name, decision.normalized_args)
                pending_payload = {
                    "task_id": task.id,
                    "tool": tool_name,
                    "args": decision.normalized_args,
                    "internal_history": history[-6:],
                    "require_edits": require_edits,
                    "tools_executed": list(self.tool_executor.executed_tools),
                    "context_override": context_override,
                }
                if tm is not None:
                    tm.store.update_pending_action(task.id, pending_payload)
                    tm.store.update_status(task.id, TaskStatus.WAITING_PERMISSION)

                # Sync to AgentState for CLI / HUD backward compatibility
                state = AgentState()
                state.pending_action = pending_payload
                state.save()
                return confirm_prompt

            # 7. Execute Tool
            exec_result = await self.tool_executor.execute(
                decision,
                step_index=current_step_idx,
                confirmed=auto_confirm or (call_sig in skip_sigs),
            )

            # 8. Record Observation
            if tm is not None:
                tm.record_observation(
                    task.id,
                    step_index=current_step_idx,
                    tool_name=tool_name,
                    args=exec_result.args,
                    result=exec_result.result if exec_result.success else (exec_result.error or exec_result.result),
                    success=exec_result.success,
                )

            if on_event:
                try:
                    on_event({
                        "event": "tool_executed",
                        "tool": tool_name,
                        "success": exec_result.success,
                        "duration_ms": exec_result.duration_ms,
                    })
                except Exception:
                    pass

            # 9. Handle Result & Recovery
            if exec_result.success:
                self.recovery_manager.record_progress()
                skip_sigs.add(call_sig)
                preview = exec_result.result[:2000]
                if tool_name in {"read_file", "list_directory", "run_command"}:
                    evidence_notes.append(f"### {tool_name}\n{preview[:1000]}")
                history.append({"role": "user", "content": f"Tool result: {preview}\n\nNext step? (JSON)"})
            else:
                rec = self.recovery_manager.evaluate(
                    task,
                    exec_result.error or exec_result.result,
                    tool_name=tool_name,
                    tool_args=args,
                )
                if tm is not None:
                    tm.record_error(
                        task.id,
                        step_index=current_step_idx,
                        error_type=rec.failure_type,
                        message=exec_result.error or exec_result.result,
                        retry_number=rec.retry_count,
                    )
                if rec.stop_execution:
                    fail_msg = f"Task halted: {rec.reason}"
                    if tm is not None:
                        tm.fail_task(task.id, fail_msg)
                    return fail_msg
                recovery_hints.append(rec.hint)
                history.append({
                    "role": "user",
                    "content": f"Tool ERROR: {exec_result.error or exec_result.result}\n\n{rec.hint}\nNext step? (JSON)",
                })

        # 10. Step limit reached
        if not require_edits and evidence_notes:
            summary = "\n\n".join(evidence_notes[-6:])
            final_report = f"Investigation completed within step limit:\n\n{summary}"
            if tm is not None:
                tm.complete_task(task.id, final_report)
            return final_report

        fallback_msg = "Task halted: step limit reached without completion."
        if tm is not None:
            tm.fail_task(task.id, fallback_msg)
        return fallback_msg
