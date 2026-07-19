"""Phase 4 self-debug orchestrator — error paste, build failures, corruption repair."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console

from editing.coding_workflow import CodingWorkflow, EditResult
from editing.error_classifier import ErrorKind, classify_error, extract_missing_module, is_error_report
from editing.patch_generator import PatchOperation

logger = logging.getLogger(__name__)
console = Console()


class SelfDebugOrchestrator:
    """Route errors to deterministic fixes or LLM-assisted repair."""

    @staticmethod
    def build_fix_patches(error_text: str, project_root: str | Path) -> list[PatchOperation]:
        root = Path(project_root).resolve()
        wf = CodingWorkflow(project_root=root)
        kind = classify_error(error_text)
        patches: list[PatchOperation] = []

        if kind == ErrorKind.RSC_MANIFEST:
            patches.extend(wf._build_nextjs_rsc_fix(include_auth=True))
        elif kind == ErrorKind.HOME_404:
            patches.extend(wf._build_missing_home_page_patch())
        elif kind == ErrorKind.LAYOUT_CORRUPTION or wf._layout_is_corrupted():
            patches.extend(wf._build_jwt_cleanup_patches())
            layout = root / "src" / "app" / "layout.tsx"
            patches.append(PatchOperation(
                path=str(layout),
                operation="write_file",
                args={"content": wf._canonical_server_layout()},
                reason="Repair corrupted layout.tsx",
            ))
            if not (root / "src" / "app" / "providers.tsx").exists():
                patches.extend(wf._build_nextjs_rsc_fix(include_auth=True))
        elif kind == ErrorKind.BUILD or kind == ErrorKind.MODULE_NOT_FOUND:
            patches.extend(wf._build_jwt_cleanup_patches())
            if wf._layout_is_corrupted():
                layout = root / "src" / "app" / "layout.tsx"
                patches.append(PatchOperation(
                    path=str(layout),
                    operation="write_file",
                    args={"content": wf._canonical_server_layout()},
                    reason="Repair layout after build failure",
                ))
            login_patch = wf._login_page_patch()
            if login_patch:
                patches.append(login_patch)

        # Deduplicate by path
        seen: set[str] = set()
        unique: list[PatchOperation] = []
        for p in patches:
            if p.path not in seen:
                seen.add(p.path)
                unique.append(p)
        return unique

    @staticmethod
    async def run_from_error(
        error_text: str,
        project_root: str | Path,
        rag_context: str = "",
    ) -> EditResult:
        """Apply deterministic fixes first, then LLM repair with error as planner hint."""
        root = Path(project_root).resolve()
        console.print("\n[bold magenta]=== Self-Debug (Phase 4) ===[/bold magenta]")
        console.print(f"[dim]Error kind: {classify_error(error_text).value}[/dim]")

        patches = SelfDebugOrchestrator.build_fix_patches(error_text, root)
        if patches:
            console.print(f"[cyan]Applying {len(patches)} deterministic debug patch(es)...[/cyan]")
            wf = CodingWorkflow(project_root=root, rag_context=rag_context)
            files_modified: list[str] = []
            for patch in patches:
                if await wf._apply_with_reflection(patch):
                    if patch.path not in files_modified:
                        files_modified.append(patch.path)

            retry = await wf._verify_build_with_retry(
                user_request=f"Fix error: {error_text[:200]}",
                files_modified=files_modified,
                planner_hint=error_text,
            )
            if retry is not None:
                return retry
            if files_modified:
                from editing.verifier import Verifier
                ok, msg = Verifier.verify_nextjs_build(root)
                if ok or msg.startswith("skip"):
                    summary = wf._build_summary("Self-debug repair", files_modified)
                    return EditResult(success=True, message=summary, files_modified=files_modified)
                return EditResult(
                    success=False,
                    message=f"Debug patches applied but build still fails:\n{msg[:1500]}",
                    files_modified=files_modified,
                )

        wf = CodingWorkflow(project_root=root, rag_context=rag_context)
        hint = error_text
        missing = extract_missing_module(error_text)
        if missing:
            hint += f"\n\nMissing module: {missing}"
        return await wf.run(
            f"Fix this error in the project:\n{error_text[:4000]}",
            planner_hint=hint,
        )

    @staticmethod
    def corruption_patches(project_root: str | Path) -> list[PatchOperation]:
        """Proactive corruption scan before any coding task."""
        root = Path(project_root).resolve()
        wf = CodingWorkflow(project_root=root)
        if not wf._layout_is_corrupted():
            return []
        console.print("[yellow]Detected layout corruption — scheduling repair[/yellow]")
        return SelfDebugOrchestrator.build_fix_patches("layout corruption detected", root)
