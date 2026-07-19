#!/usr/bin/env python3
"""Verify JWT implementation workflow on SkillLens (or temp copy)."""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKILLLENS = Path(r"C:\Users\reyan\OneDrive\Desktop\SkillLens")
USE_LIVE = "--live" in sys.argv


async def main() -> int:
    if USE_LIVE:
        project = SKILLLENS
        print(f"Running LIVE on {project}")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="skilllens_jwt_test_"))
        print(f"Copying SkillLens to {tmp} ...")
        shutil.copytree(SKILLLENS, tmp, dirs_exist_ok=True)
        project = tmp

    from editing.coding_workflow import CodingWorkflow

    workflow = CodingWorkflow(project_root=project, rag_context="")
    patches = workflow._build_jwt_auth_patches()
    if not patches:
        print("JWT scaffold not applicable (auth may already exist)")
        return 1

    print(f"Applying {len(patches)} JWT scaffold patches (no LLM)...")
    files_modified = []
    for patch in patches:
        ok = await workflow._apply_with_reflection(patch)
        if ok:
            files_modified.append(patch.path)

    result_success = len(files_modified) >= 5
    result_msg = f"Modified {len(files_modified)} files via JWT scaffold"

    checks = {
        "backend/main.py": (project / "backend" / "main.py").exists(),
        "backend/requirements.txt": (project / "backend" / "requirements.txt").exists(),
        "src/context/auth-context.tsx": (project / "src" / "context" / "auth-context.tsx").exists(),
        "src/app/login/page.tsx": (project / "src" / "app" / "login" / "page.tsx").exists(),
        "src/lib/api.ts": (project / "src" / "lib" / "api.ts").exists(),
        "layout has AuthProvider": "AuthProvider" in (project / "src" / "app" / "layout.tsx").read_text(encoding="utf-8"),
    }

    print("\n=== RESULT ===")
    print(f"Success: {result_success}")
    print(f"Message: {result_msg}")
    print(f"Files modified: {len(files_modified)}")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}: {name}")

    # Syntax check backend
    if checks["backend/main.py"]:
        import ast
        try:
            ast.parse((project / "backend" / "main.py").read_text(encoding="utf-8"))
            print("  PASS: backend/main.py Python syntax")
        except SyntaxError as e:
            print(f"  FAIL: backend syntax: {e}")
            return 1

    from editing.patch_validator import PatchValidator
    for tsx in ["src/context/auth-context.tsx", "src/app/login/page.tsx", "src/app/layout.tsx"]:
        p = project / tsx
        if p.exists():
            valid, msg = PatchValidator.validate_syntax(p.read_text(encoding="utf-8"), tsx)
            print(f"  {'PASS' if valid else 'FAIL'}: {tsx} ({msg})")

    all_ok = result_success and all(checks.values())
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
