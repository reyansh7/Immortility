"""Tests for path sanitization and JWT scaffold."""

from pathlib import Path

from core.paths import sanitize_llm_path, is_path_in_project
from editing.patch_generator import LLMPatchGenerator


def test_reject_hallucinated_paths(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.tsx").write_text("x", encoding="utf-8")

    bad = "/home/runner/work/ai-recruiting-platform/src/app/dashboard/page.tsx"
    assert sanitize_llm_path(bad, root) is None

    good = sanitize_llm_path("src/app.tsx", root)
    assert good is not None
    assert is_path_in_project(good, root)


def test_parse_rejects_foreign_paths(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    gen = LLMPatchGenerator()
    raw = """[{"path":"/home/runner/work/foo/src/app/page.tsx","operation":"edit_file","args":{"target_text":"a","replacement_text":"b"}}]"""
    patches = gen._parse_patch_response(raw, root)
    assert patches == []


def test_jwt_scaffold_paths(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "layout.tsx").write_text(
        'import { CandidatesProvider } from "@/context/candidates-context";\n'
        'import { ThemeProvider } from "@/components/theme-provider";\n'
        'import { Navbar } from "@/components/layout/navbar";\n'
        "export default function L({ children }) { return (\n"
        "  <ThemeProvider><CandidatesProvider><Navbar />{children}</CandidatesProvider></ThemeProvider>\n"
        " ); }\n",
        encoding="utf-8",
    )
    from editing.coding_workflow import CodingWorkflow
    wf = CodingWorkflow(project_root=tmp_path)
    patches = wf._build_jwt_auth_patches()
    assert len(patches) >= 5
    paths = [Path(p.path) for p in patches]
    assert all(is_path_in_project(str(p), tmp_path) for p in paths)
    providers = tmp_path / "src" / "app" / "providers.tsx"
    assert any(p.name == "providers.tsx" for p in paths) or providers.name in [Path(x.path).name for x in patches]


def test_jwt_scaffold_upgrades_partial_auth(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "src" / "context").mkdir(parents=True)
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "app" / "login").mkdir(parents=True)

    (tmp_path / "backend" / "main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\nUSERS = {"admin@skilllens.com": "password123"}\n',
        encoding="utf-8",
    )
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\npython-jose[cryptography]\n", encoding="utf-8")
    (tmp_path / "src" / "context" / "auth-context.tsx").write_text(
        '"use client";\nexport function AuthProvider({ children }) { return children; }\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "lib" / "api.ts").write_text(
        'export async function loginUser() { return "token"; }\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "app" / "login" / "page.tsx").write_text(
        '"use client";\nexport default function LoginPage() { return <div>Login</div>; }\n',
        encoding="utf-8",
    )

    from editing.coding_workflow import CodingWorkflow
    from editing.edit_planner import EditPlan

    wf = CodingWorkflow(project_root=tmp_path)
    patches = wf._build_patches_from_request(
        "Implement JWT authentication with a FastAPI backend and register support",
        plan=EditPlan(
            goal="", files_to_read=[], files_to_edit=[], dependencies=[],
            risks=[], verification_strategy="", symbols_to_modify=[], plan_steps=[],
        ),
    )

    rel_paths = {Path(p.path).relative_to(tmp_path).as_posix() for p in patches}
    assert "backend/main.py" in rel_paths
    assert "backend/requirements.txt" in rel_paths
    assert "src/context/auth-context.tsx" in rel_paths
    assert "src/lib/api.ts" in rel_paths
    assert "src/app/login/page.tsx" in rel_paths
    assert all("candidates/page.tsx" not in path for path in rel_paths)


def test_runtime_error_detection():
    from editing.coding_workflow import CodingWorkflow
    wf = CodingWorkflow()
    err = (
        "Error TypeRuntime Error\n"
        "Could not find the module theme-provider.tsx#ThemeProvider in the React Client Manifest\n"
        "digest: '1113300095'"
    )
    assert wf._is_runtime_error_report(err)


def test_nextjs_rsc_fix(tmp_path):
    app = tmp_path / "src" / "app"
    app.mkdir(parents=True)
    (app / "layout.tsx").write_text(
        'import type { Metadata } from "next";\n'
        'import { ThemeProvider } from "@/components/theme-provider";\n'
        'import { AuthProvider } from "@/context/auth-context";\n'
        'import { Navbar } from "@/components/layout/navbar";\n'
        'export default function RootLayout({ children }) {\n'
        '  return (\n'
        '    <html><body><ThemeProvider><AuthProvider><Navbar />{children}</AuthProvider></ThemeProvider></body></html>\n'
        '  );\n'
        '}\n',
        encoding="utf-8",
    )
    from editing.coding_workflow import CodingWorkflow
    wf = CodingWorkflow(project_root=tmp_path)
    patches = wf._build_nextjs_rsc_fix(include_auth=True)
    assert len(patches) == 2
    layout = (app / "layout.tsx").read_text(encoding="utf-8")
    new_layout = wf._server_layout_content((app / "layout.tsx").read_text(encoding="utf-8"))
    assert "AppProviders" in new_layout
    assert "ThemeProvider" not in new_layout
