"""Patch generation — unified diffs and LLM-driven patch operations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# pyrefly: ignore [missing-import]
from ollama import chat

from core.paths import FileContentIndex, normalize_path_key, sanitize_llm_path, to_rel_path
from editing.edit_planner import EditPlan

logger = logging.getLogger(__name__)


@dataclass
class PatchOperation:
    path: str
    operation: str
    args: dict[str, Any]
    reason: str = ""
    confidence: float = 1.0
    risk_level: str = "low"


class PatchGenerator:
    """Static helpers for diff generation."""

    @staticmethod
    def generate_unified_diff(
        old_text: str,
        new_text: str,
        fromfile: str = "original",
        tofile: str = "modified",
    ) -> str:
        import difflib
        diff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
        )
        return "".join(diff)


class LLMPatchGenerator:
    """Uses Qwen to generate concrete patch operations in a single batched call."""

    VALID_OPERATIONS = frozenset({
        "create_file", "write_file", "edit_file", "replace_lines", "append_file",
        "insert_before", "insert_after", "replace_regex", "delete_block",
        "rename_symbol",
    })

    async def generate_patches(
        self,
        user_request: str,
        edit_plan: EditPlan,
        project_root: Path,
        file_index: FileContentIndex,
        project_structure: str = "",
        rag_context: str = "",
        protect_layout: bool = False,
    ) -> list[PatchOperation]:
        """One LLM call producing all patches for the task."""
        project_root = project_root.resolve()
        edit_paths = list(dict.fromkeys(edit_plan.files_to_edit or []))

        # Always infer paths from steps to catch newly proposed files like 'Login.js'
        if edit_plan.plan_steps:
            inferred = self._infer_paths_from_steps(edit_plan.plan_steps, project_root)
            for p in inferred:
                if p not in edit_paths:
                    edit_paths.append(p)

        files_section = self._format_all_files(edit_paths, file_index, project_root)
        allowed_paths = self._allowed_paths_list(project_root, project_structure)

        prompt = f"""You are the Patch Generator. Output REAL executable patches as a JSON array.
Your ONLY job is to execute the Plan steps to fulfill the User Request.

CRITICAL USER REQUEST: {user_request}
Goal: {edit_plan.goal}

Plan steps:
{chr(10).join(f'- {s}' for s in edit_plan.plan_steps)}

Files to create or edit:
{chr(10).join(f'- {p}' for p in edit_paths) if edit_paths else '- (determine from plan)'}

Project root: {project_root}

ALLOWED PATHS (use ONLY these relative paths — never use /home/, /Users/, or other projects):
{allowed_paths}

Project tree:
{project_structure[:4000]}

Context (background only — do NOT edit unrelated files mentioned here):
{rag_context[:3000]}

FILE CONTENTS (line numbers added for reference):
{files_section}

RULES:
1. Output ONLY a JSON array. No markdown, no prose.
2. Paths MUST be relative to project root (e.g. src/app/login/page.tsx, backend/main.py).
3. NEVER use absolute paths or paths from other repositories.
4. NEW files -> create_file with args.content (full file).
5. EXISTING files -> ALWAYS prefer `replace_lines` using the line numbers provided in FILE CONTENTS. `edit_file` often fails due to indentation/whitespace mismatches.
6. When using `replace_lines` or `create_file`, NEVER include line numbers (e.g., '1: ') in your replacement content.
7. Include ALL files needed for the user request (frontend + backend).

Each object must use these exact arg keys:
- create_file: {{"content": "full file text"}}
- replace_lines: {{"start_line": 1, "end_line": 5, "replacement": "new lines without line numbers"}}
- edit_file: {{"target_text": "exact text to replace (WARNING: extremely prone to whitespace failure, use replace_lines instead)", "replacement_text": "new text"}}
- insert_after: {{"target_text": "exact single anchor line", "content": "lines to insert"}}
- write_file: {{"content": "full file text"}}

Each object: {{"path": "...", "operation": "...", "reason": "...", "confidence": 0.9, "risk_level": "low", "args": {{...}}}}

Example output:
[
  {{
    "path": "path/to/file.py",
    "operation": "create_file",
    "reason": "Create new module. Self-review: imports are correct, structure is sound.",
    "confidence": 0.95,
    "risk_level": "low",
    "args": {{"content": "def example():\n    pass\n"}}
  }}
]

JSON array:"""

        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    chat,
                    model="qwen3:8b",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You output only valid JSON arrays of patch objects. "
                                "No markdown fences. No explanations. "
                                "CRITICAL: You MUST escape all newlines (\\n) and double quotes (\\\") inside your JSON strings."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    think=False,
                )
                raw = response["message"]["content"]
                logger.debug("Patch LLM raw (attempt %d): %s", attempt + 1, raw[:500])

                patches = self._parse_patch_response(raw, project_root)
                if patches:
                    if protect_layout:
                        protected = frozenset({"src/app/layout.tsx", "src/app/providers.tsx"})
                        patches = [
                            p for p in patches
                            if to_rel_path(p.path, project_root).replace("\\", "/") not in protected
                        ]
                    if patches:
                        return patches
                logger.warning("Patch parse attempt %d returned 0 patches", attempt + 1)
            except Exception as exc:
                logger.error("Patch generation attempt %d failed: %s", attempt + 1, exc)

        return []

    def _format_all_files(
        self,
        edit_paths: list[str],
        file_index: FileContentIndex,
        project_root: Path,
    ) -> str:
        parts: list[str] = []
        seen: set[str] = set()

        for rel in edit_paths:
            content = file_index.get(rel)
            if content is not None:
                rel_key = to_rel_path(rel, project_root) if not rel.startswith("(") else rel
                if rel_key not in seen:
                    seen.add(rel_key)
                    numbered = "\n".join(
                        f"{i + 1}: {line}" for i, line in enumerate(content.splitlines())
                    )
                    parts.append(f"=== {rel_key} (EXISTS) ===\n{numbered}\n")

        for rel, content in file_index.all_for_prompt().items():
            if rel not in seen and len(parts) < 12:
                seen.add(rel)
                numbered = "\n".join(
                    f"{i + 1}: {line}" for i, line in enumerate(content.splitlines()[:200])
                )
                parts.append(f"=== {rel} (EXISTS) ===\n{numbered}\n")

        if not parts:
            return "(No existing files loaded — use create_file for new files)"
        return "\n".join(parts)[:14000]

    @staticmethod
    def _infer_paths_from_steps(steps: list[str], project_root: Path) -> list[str]:
        paths: list[str] = []
        pattern = re.compile(r"[\w./\\-]+\.(?:py|tsx?|jsx?|json|md)\b")
        for step in steps:
            for match in pattern.findall(step):
                p = match.replace("\\", "/")
                if p not in paths:
                    paths.append(p)
        return paths[:15]

    @staticmethod
    def _allowed_paths_list(project_root: Path, project_structure: str) -> str:
        lines = [ln.strip() for ln in project_structure.splitlines() if ln.strip()]
        if not lines:
            root = Path(project_root)
            lines = [
                to_rel_path(p, root)
                for p in sorted(root.rglob("*"))
                if p.is_file() and p.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".json"}
            ][:40]
        return "\n".join(f"- {p}" for p in lines[:40]) or "- (use paths from project tree)"

    def _parse_patch_response(self, raw: str, project_root: Path) -> list[PatchOperation]:
        content = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
        if "```json" in content:
            content = content.split("```json")[-1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        data = self._extract_json_array(content)
        if data is None:
            data = self._extract_json_objects(content)
        if data is None:
            logger.warning("Could not parse patch JSON from LLM response (first 500 chars): %s", content[:500])
            return []

        patches: list[PatchOperation] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            op_name = item.get("operation", "")
            if op_name not in self.VALID_OPERATIONS:
                continue
            path = item.get("path", "")
            if not path:
                continue
            resolved = sanitize_llm_path(path, project_root)
            if not resolved:
                logger.warning("Rejected hallucinated path: %s", path)
                continue
            patches.append(PatchOperation(
                path=resolved,
                operation=op_name,
                args=dict(item.get("args", {})),
                reason=item.get("reason", ""),
                confidence=float(item.get("confidence", 0.8)),
                risk_level=item.get("risk_level", "medium"),
            ))

        logger.info("Parsed %d patches from LLM", len(patches))
        return patches

    @staticmethod
    def _extract_json_objects(content: str) -> list | None:
        """Fallback: collect top-level {...} objects if array parse fails."""
        objects: list = []
        i = 0
        while i < len(content):
            if content[i] != "{":
                i += 1
                continue
            depth = 0
            for j in range(i, len(content)):
                if content[j] == "{":
                    depth += 1
                elif content[j] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(content[i : j + 1])
                            if isinstance(obj, dict) and obj.get("operation"):
                                objects.append(obj)
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            else:
                break
        return objects or None

    @staticmethod
    def _extract_json_array(content: str) -> list | None:
        """Extract JSON array with balanced-bracket parsing."""
        start = content.find("[")
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(content)):
            if content[i] == "[":
                depth += 1
            elif content[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(content[start : i + 1])
                    except json.JSONDecodeError:
                        return None
        return None
