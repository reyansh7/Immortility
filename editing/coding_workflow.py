"""
Coding Workflow — read → plan → patch → verify → reflect pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from core.agent_state import AgentState
from core.paths import FileContentIndex, normalize_path_key, to_rel_path
from editing.edit_planner import EditPlanner, EditPlan
from editing.file_editor import FileEditor
from editing.patch_generator import LLMPatchGenerator, PatchOperation
from editing.patch_args import normalize_patch_args
from editing.reflection_engine import ReflectionEngine
from editing.verifier import Verifier
from tools.file_tool import FileTool
from memory.experience_memory import ExperienceMemory

logger = logging.getLogger(__name__)
console = Console()

SKIP_DIRS = {
    ".git", "venv", "node_modules", "__pycache__", ".venv",
    "vector_db", "logs", ".pytest_cache", ".next", "dist", "build",
}
CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md"}

# Files created by mistaken LLM patches during JWT tasks — always remove
JWT_BAD_REL_PATHS = (
    "src/app/auth/api/login.ts",
    "src/app/auth/page.tsx",
    "src/lib/auth.ts",
    "src/lib/db.ts",
)

LLM_PROTECTED_PATHS = frozenset({
    "src/app/layout.tsx",
    "src/app/providers.tsx",
})


@dataclass
class EditResult:
    success: bool
    message: str
    files_modified: list[str] = field(default_factory=list)
    failed_patches: list[str] = field(default_factory=list)


class CodingWorkflow:
    MAX_RETRIES = 3
    _structure_cache: dict[str, tuple[float, str]] = {}

    def __init__(self, project_root: str | Path | None = None, rag_context: str = ""):
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd()
        self.rag_context = rag_context
        self.state = AgentState()
        self.edit_planner = EditPlanner()
        self.patch_generator = LLMPatchGenerator()
        self.reflection = ReflectionEngine(max_retries=self.MAX_RETRIES)
        self.experience_memory = ExperienceMemory()
        self.file_index = FileContentIndex(self.project_root)
        self._files_read: set[str] = set()

    async def run(self, user_request: str, planner_hint: str = "") -> EditResult:
        console.print("\n[bold cyan]=== Coding Workflow ===[/bold cyan]")
        logger.info("Starting coding workflow: %s", user_request)

        self.state.current_task = {"goal": user_request, "workflow": "coding", "step": "Planning"}
        self.state.save()

        from editing.self_debug import SelfDebugOrchestrator

        corruption = SelfDebugOrchestrator.corruption_patches(self.project_root)
        if corruption:
            console.print(f"[yellow]Repairing {len(corruption)} corrupted file(s) before edits[/yellow]")
            for patch in corruption:
                await self._apply_with_reflection(patch)

        lower_req = user_request.lower()
        is_jwt = self._is_jwt_task(lower_req)

        # Fast path: deterministic patches (JWT, rename, logging) skip LLM planner
        quick_patches = self._build_patches_from_request(user_request, EditPlan(
            goal=user_request, files_to_read=[], files_to_edit=[],
            dependencies=[], risks=[], verification_strategy="",
            symbols_to_modify=[], plan_steps=[],
        ))

        if is_jwt:
            jwt_patches = self._build_jwt_auth_patches()
            if jwt_patches:
                quick_patches = jwt_patches
            console.print(
                f"[cyan]JWT task — deterministic scaffold only ({len(quick_patches)} patches), "
                "LLM planner skipped[/cyan]"
            )
        elif quick_patches:
            console.print(f"[cyan]Using deterministic patches ({len(quick_patches)}) - skipping LLM planner[/cyan]")

        if quick_patches:
            edit_plan = EditPlan(
                goal=user_request,
                files_to_read=[],
                files_to_edit=[to_rel_path(p.path, self.project_root) for p in quick_patches],
                dependencies=[], risks=[], verification_strategy="syntax check",
                symbols_to_modify=[], plan_steps=["Apply scaffold patches"],
            )
            patches = quick_patches
        else:
            edit_plan = await self.edit_planner.generate_plan(
                user_request,
                self.rag_context + (f"\n\n{planner_hint}" if planner_hint else ""),
            )
            edit_plan = self._merge_planner_hint(edit_plan, planner_hint)
            console.print(f"[green]Plan:[/green] {edit_plan.goal}")
            for step in edit_plan.plan_steps:
                console.print(f"  - {step}")

            self._read_plan_files(edit_plan)

            patches = self._build_patches_from_request(user_request, edit_plan)
            if not patches and not is_jwt:
                console.print("[cyan]Generating patches with Qwen (single batch)...[/cyan]")
                patches = await self.patch_generator.generate_patches(
                    user_request=user_request,
                    edit_plan=edit_plan,
                    project_root=self.project_root,
                    file_index=self.file_index,
                    project_structure=self._project_structure(),
                    rag_context=self.rag_context,
                    protect_layout=is_jwt or "jwt" in lower_req or "authentication" in lower_req,
                )
            elif is_jwt:
                patches = quick_patches

        if not patches:
            if is_jwt and not self._layout_is_corrupted():
                backend = self.project_root / "backend" / "main.py"
                login = self.project_root / "src" / "app" / "login" / "page.tsx"
                if backend.exists() and login.exists():
                    return EditResult(
                        success=True,
                        message="JWT authentication is already configured. No changes needed.",
                        files_modified=[],
                    )
            return EditResult(
                success=False,
                message="No patches generated. Falling back to Action Engine recommended.",
            )

        console.print(f"[cyan]Applying {len(patches)} patch(es)...[/cyan]")
        files_modified: list[str] = []
        failed: list[str] = []

        for i, patch in enumerate(patches, 1):
            rel = to_rel_path(patch.path, self.project_root)
            console.print(
                f"  [{i}/{len(patches)}] {patch.operation} -> {rel}"
                + (f" — {patch.reason}" if patch.reason else "")
            )
            if await self._apply_with_reflection(patch):
                if patch.path not in files_modified:
                    files_modified.append(patch.path)
            else:
                failed.append(rel)
                console.print(f"[yellow]  x failed: {rel}[/yellow]")

        if failed:
            msg = (
                f"Applied {len(files_modified)}/{len(patches)} patches. "
                f"Failed: {', '.join(failed)}"
            )
            if not files_modified:
                return EditResult(success=False, message=msg, failed_patches=failed)
            return EditResult(
                success=False,
                message=msg,
                files_modified=files_modified,
                failed_patches=failed,
            )

        for filepath in files_modified:
            if Verifier.verify_file(filepath, project_root=self.project_root) == "FAIL":
                FileEditor.rollback(filepath)
                return EditResult(
                    success=False,
                    message=f"Verification failed for {to_rel_path(filepath, self.project_root)}, rolled back",
                    files_modified=[],
                )

        build_result = await self._verify_build_with_retry(
            user_request, files_modified, planner_hint=planner_hint
        )
        if build_result is not None:
            return build_result

        self.state.current_task["step"] = "Complete"
        self.state.save()
        summary = self._build_summary(user_request, files_modified)
        # Phase 2: refresh AST summaries for edited files
        try:
            from knowledge.engine import KnowledgeEngine
            ke = KnowledgeEngine()
            proj = ke.get_active_project_name() or self.project_root.name
            for fp in files_modified:
                ke._hierarchical.ingest_file(fp, proj)
            if files_modified:
                ke._hierarchical.rebuild_folder_summaries(proj, self.project_root)
                ke._hierarchical.rebuild_repo_summary(proj, self.project_root)
        except Exception:
            pass
        return EditResult(success=True, message=summary, files_modified=files_modified)

    async def _verify_build_with_retry(
        self,
        user_request: str,
        files_modified: list[str],
        planner_hint: str = "",
    ) -> EditResult | None:
        """Run npm build with deterministic + LLM retries. None means success."""
        from editing.self_debug import SelfDebugOrchestrator

        max_attempts = self.MAX_RETRIES
        last_msg = ""

        for attempt in range(max_attempts):
            ok, msg = Verifier.verify_nextjs_build(self.project_root)
            if ok:
                if msg == "npm run build passed":
                    console.print("[green]npm run build passed[/green]")
                return None

            last_msg = msg
            console.print(
                f"[yellow]Build failed (attempt {attempt + 1}/{max_attempts})[/yellow]"
            )

            if attempt >= max_attempts - 1:
                break

            experiences = self.experience_memory.get_relevant_experiences(msg, limit=3)
            exp_hint = "\n".join(e.fix_applied for e in experiences if e.fix_applied)

            debug_patches = SelfDebugOrchestrator.build_fix_patches(msg, self.project_root)
            applied = False
            for patch in debug_patches:
                if await self._apply_with_reflection(patch):
                    if patch.path not in files_modified:
                        files_modified.append(patch.path)
                    applied = True

            if not applied:
                hint = f"{planner_hint}\n\nBuild output:\n{msg[-3000:]}"
                if exp_hint:
                    hint += f"\n\nPast fixes:\n{exp_hint}"
                edit_plan = EditPlan(
                    goal=f"Fix build failure: {user_request[:200]}",
                    files_to_read=[],
                    files_to_edit=[],
                    dependencies=[],
                    risks=[],
                    verification_strategy="npm run build",
                    symbols_to_modify=[],
                    plan_steps=["Fix build errors from compiler output"],
                )
                extra = await self.patch_generator.generate_patches(
                    user_request=f"Fix this build error:\n{msg[-2500:]}",
                    edit_plan=edit_plan,
                    project_root=self.project_root,
                    file_index=self.file_index,
                    project_structure=self._project_structure(),
                    rag_context=hint,
                    protect_layout=True,
                )
                for patch in extra:
                    if await self._apply_with_reflection(patch):
                        if patch.path not in files_modified:
                            files_modified.append(patch.path)
                        applied = True

            if not applied:
                break

        if last_msg and not last_msg.startswith("skip"):
            return EditResult(
                success=False,
                message=f"npm run build failed after {max_attempts} attempts:\n{last_msg[-2000:]}",
                files_modified=files_modified,
            )
        return None

    def _merge_planner_hint(self, edit_plan: EditPlan, planner_hint: str) -> EditPlan:
        if not planner_hint:
            return edit_plan
        try:
            content = planner_hint
            if "```json" in content:
                content = content.split("```json")[-1].split("```")[0].strip()
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                return edit_plan
            data = json.loads(match.group())
            files_read = list(dict.fromkeys(edit_plan.files_to_read + data.get("files_to_read", [])))
            files_edit = list(dict.fromkeys(edit_plan.files_to_edit + data.get("files_to_edit", [])))
            steps = list(edit_plan.plan_steps)
            for s in data.get("plan_steps", data.get("steps", [])):
                if isinstance(s, dict):
                    steps.append(s.get("description", str(s)))
                elif s and s not in steps:
                    steps.append(str(s))
            return EditPlan(
                goal=edit_plan.goal or data.get("goal", ""),
                files_to_read=files_read,
                files_to_edit=files_edit,
                dependencies=edit_plan.dependencies,
                risks=edit_plan.risks,
                verification_strategy=edit_plan.verification_strategy,
                symbols_to_modify=edit_plan.symbols_to_modify,
                plan_steps=steps,
            )
        except (json.JSONDecodeError, TypeError):
            return edit_plan

    def _read_plan_files(self, plan: EditPlan) -> None:
        paths: set[str] = set(plan.files_to_read + plan.files_to_edit)
        for rel in self._discover_relevant_files(plan):
            paths.add(rel)
        for filepath in paths:
            resolved = normalize_path_key(filepath, self.project_root)
            if Path(resolved).is_file():
                result = FileTool.read_file(resolved)
                if result["status"] == "success":
                    self._files_read.add(resolved)
                    self.file_index.add(resolved, result.get("raw", ""))

    def _discover_relevant_files(self, plan: EditPlan) -> list[str]:
        found: list[str] = []
        for rel in set(plan.files_to_edit + plan.files_to_read):
            if (self.project_root / rel).exists():
                found.append(rel.replace("\\", "/"))
        keywords = ("auth", "login", "layout", "api", "context", "middleware", "page", "main")
        count = 0
        for path in self.project_root.rglob("*"):
            if count >= 15:
                break
            if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
                continue
            if any(s in path.parts for s in SKIP_DIRS):
                continue
            rel = to_rel_path(path, self.project_root)
            if any(kw in rel.lower() for kw in keywords):
                found.append(rel)
                count += 1
        return found

    def _project_structure(self, limit: int = 80) -> str:
        cache_key = str(self.project_root)
        import time
        mtime = self.project_root.stat().st_mtime
        cached = self._structure_cache.get(cache_key)
        if cached and cached[0] == mtime:
            return cached[1]
        lines: list[str] = []
        for path in sorted(self.project_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
                continue
            if any(s in path.parts for s in SKIP_DIRS):
                continue
            lines.append(to_rel_path(path, self.project_root))
            if len(lines) >= limit:
                break
        result = "\n".join(lines)
        self._structure_cache[cache_key] = (mtime, result)
        return result

    def _is_rsc_manifest_error(self, request: str) -> bool:
        lower = request.lower()
        return (
            "react client manifest" in lower
            or "rsc manifest" in lower
            or "manifest file is empty" in lower
            or ("could not find the module" in lower and "#" in request)
            or ("digest:" in lower and "stringify" in lower)
        )

    def _is_runtime_error_report(self, request: str) -> bool:
        return self._is_rsc_manifest_error(request)

    def _is_home_404_issue(self, lower: str) -> bool:
        return (
            ("get /" in lower and "404" in lower)
            or ("failed to load resource" in lower and "404" in lower)
            or ("missing" in lower and "page.tsx" in lower)
            or ("root route" in lower or "src/app/page.tsx" in lower)
        )

    def _is_login_navbar_task(self, lower: str) -> bool:
        return (
            ("login" in lower and any(k in lower for k in ("navbar", "button", "page", "frontend", "nav")))
            or ("navbar" in lower and "login" in lower)
        )

    def _is_jwt_task(self, lower: str) -> bool:
        return (
            "jwt" in lower
            or ("fastapi" in lower and any(k in lower for k in ("auth", "authentication", "login", "backend")))
            or ("implement" in lower and any(k in lower for k in ("authentication", "login", "backend", "jwt")))
            or self._is_login_navbar_task(lower)
        )

    def _build_patches_from_request(self, request: str, plan: EditPlan) -> list[PatchOperation]:
        lower = request.lower()

        if self._is_runtime_error_report(request):
            rsc_fix = self._build_nextjs_rsc_fix(include_auth=True)
            if rsc_fix:
                console.print(f"[cyan]Using Next.js RSC debug fix: {len(rsc_fix)} deterministic patches[/cyan]")
                return rsc_fix

        if self._is_home_404_issue(lower):
            home_fix = self._build_missing_home_page_patch()
            if home_fix:
                console.print(f"[cyan]Using missing home page fix: {len(home_fix)} patch(es)[/cyan]")
                return home_fix

        patches: list[PatchOperation] = []

        if self._is_jwt_task(lower) and any(
            k in lower for k in ("implement", "add", "create", "build", "setup", "fastapi", "fix", "login", "navbar")
        ):
            jwt_patches = self._build_jwt_auth_patches()
            if jwt_patches:
                return jwt_patches

        if self._is_login_navbar_task(lower):
            nav_patches = self._build_login_navbar_patches()
            if nav_patches:
                console.print(f"[cyan]Using login + navbar scaffold: {len(nav_patches)} patch(es)[/cyan]")
                return nav_patches

        if "rename" in lower and "login" in lower and "authenticate" in lower:
            for fpath in self._find_files_with_symbol("login"):
                patches.append(PatchOperation(
                    path=normalize_path_key(fpath, self.project_root),
                    operation="rename_symbol",
                    args={"old_name": "login", "new_name": "authenticate"},
                    reason="Rename login → authenticate",
                ))
        if "logging" in lower and "detect" in lower:
            detect = normalize_path_key("detect.py", self.project_root)
            if Path(detect).exists():
                content = Path(detect).read_text(encoding="utf-8")
                if "import logging" not in content:
                    patches.append(PatchOperation(
                        path=detect, operation="insert_after",
                        args={"target_text": "from ultralytics import YOLO",
                              "content": "import logging\n\nlogger = logging.getLogger(__name__)"},
                        reason="Add logging import",
                    ))
                if "logger.info" not in content:
                    patches.append(PatchOperation(
                        path=detect, operation="insert_after",
                        args={"target_text": "    def detect(self, image):",
                              "content": '        logger.info("Running YOLO detection")'},
                        reason="Add log line",
                    ))
        return patches

    @staticmethod
    def _canonical_server_layout() -> str:
        return (
            'import type { Metadata } from "next";\n'
            'import { Geist, Geist_Mono } from "next/font/google";\n'
            'import { AppProviders } from "./providers";\n'
            'import "./globals.css";\n\n'
            'const geistSans = Geist({\n'
            '  variable: "--font-geist-sans",\n'
            '  subsets: ["latin"],\n'
            '});\n\n'
            'const geistMono = Geist_Mono({\n'
            '  variable: "--font-geist-mono",\n'
            '  subsets: ["latin"],\n'
            '});\n\n'
            'export const metadata: Metadata = {\n'
            '  title: "TalentLens — AI-Powered Resume Analysis & Candidate Ranking",\n'
            '  description:\n'
            '    "Screen resumes faster with AI-powered parsing, skill matching, candidate ranking, and fraud detection.",\n'
            '};\n\n'
            'export default function RootLayout({\n'
            '  children,\n'
            '}: Readonly<{\n'
            '  children: React.ReactNode;\n'
            '}>) {\n'
            '  return (\n'
            '    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full`}>\n'
            '      <body className="min-h-full flex flex-col antialiased">\n'
            '        <AppProviders>{children}</AppProviders>\n'
            '      </body>\n'
            '    </html>\n'
            '  );\n'
            '}\n'
        )

    def _layout_is_corrupted(self) -> bool:
        layout = self.project_root / "src" / "app" / "layout.tsx"
        if not layout.exists():
            return True
        content = layout.read_text(encoding="utf-8", errors="replace")
        if content.count("export default function RootLayout") > 1:
            return True
        if content.count("export const metadata") > 1:
            return True
        if "useState" in content or "useAuth(" in content:
            return True
        if "AppProviders" not in content:
            return True
        if "JobBoard" in content or "@/lib/db" in content:
            return True
        return False

    def _build_jwt_cleanup_patches(self) -> list[PatchOperation]:
        patches: list[PatchOperation] = []
        for rel in JWT_BAD_REL_PATHS:
            path = self.project_root / rel.replace("/", "\\")
            if path.exists():
                patches.append(PatchOperation(
                    path=str(path),
                    operation="delete_file",
                    args={},
                    reason="Remove hallucinated auth file from bad LLM patch",
                ))
        return patches

    @staticmethod
    def _file_missing_or_lacks(path: Path, required_markers: tuple[str, ...]) -> bool:
        """Return True when a file is absent or missing required scaffold markers."""
        if not path.exists():
            return True
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
        return any(marker not in content for marker in required_markers)

    def _build_jwt_auth_patches(self) -> list[PatchOperation]:
        """Deterministic JWT + FastAPI scaffold for Next.js projects without auth."""
        root = self.project_root
        auth_ctx = root / "src" / "context" / "auth-context.tsx"
        login_page = root / "src" / "app" / "login" / "page.tsx"
        backend_main = root / "backend" / "main.py"
        api_lib = root / "src" / "lib" / "api.ts"
        layout = root / "src" / "app" / "layout.tsx"

        patches: list[PatchOperation] = []

        backend_content = (
                'import os\n'
                'from datetime import datetime, timedelta, timezone\n\n'
                'from fastapi import Depends, FastAPI, HTTPException, status\n'
                'from fastapi.middleware.cors import CORSMiddleware\n'
                'from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm\n'
                'from jose import JWTError, jwt\n'
                'from passlib.context import CryptContext\n'
                'from pydantic import BaseModel, EmailStr\n\n'
                'SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me-in-production")\n'
                'ALGORITHM = "HS256"\n'
                'ACCESS_TOKEN_EXPIRE_MINUTES = 60\n\n'
                'app = FastAPI(title="SkillLens Auth API")\n'
                'app.add_middleware(\n'
                '    CORSMiddleware,\n'
                '    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")],\n'
                '    allow_credentials=True,\n'
                '    allow_methods=["*"],\n'
                '    allow_headers=["*"],\n'
                ')\n\n'
                'pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")\n'
                'oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")\n'
                'fake_users_db: dict[str, dict[str, str]] = {\n'
                '    "admin@skilllens.com": {\n'
                '        "email": "admin@skilllens.com",\n'
                '        "hashed_password": pwd_context.hash("password123"),\n'
                '    }\n'
                '}\n\n\n'
                'class UserCreate(BaseModel):\n'
                '    email: EmailStr\n'
                '    password: str\n\n\n'
                'class UserPublic(BaseModel):\n'
                '    email: EmailStr\n\n\n'
                'class Token(BaseModel):\n'
                '    access_token: str\n'
                '    token_type: str = "bearer"\n\n\n'
                'def verify_password(plain_password: str, hashed_password: str) -> bool:\n'
                '    return pwd_context.verify(plain_password, hashed_password)\n\n\n'
                'def hash_password(password: str) -> str:\n'
                '    return pwd_context.hash(password)\n\n\n'
                'def create_access_token(subject: str) -> str:\n'
                '    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)\n'
                '    return jwt.encode({"sub": subject, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)\n\n\n'
                'def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:\n'
                '    credentials_error = HTTPException(\n'
                '        status_code=status.HTTP_401_UNAUTHORIZED,\n'
                '        detail="Could not validate credentials",\n'
                '        headers={"WWW-Authenticate": "Bearer"},\n'
                '    )\n'
                '    try:\n'
                '        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])\n'
                '        email = payload.get("sub")\n'
                '        if not email or email not in fake_users_db:\n'
                '            raise credentials_error\n'
                '    except JWTError as exc:\n'
                '        raise credentials_error from exc\n'
                '    return UserPublic(email=email)\n\n\n'
                '@app.get("/health")\n'
                'def health():\n'
                '    return {"status": "ok"}\n\n\n'
                '@app.post("/auth/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED)\n'
                'def register(payload: UserCreate):\n'
                '    email = payload.email.lower()\n'
                '    if email in fake_users_db:\n'
                '        raise HTTPException(status_code=409, detail="User already exists")\n'
                '    fake_users_db[email] = {"email": email, "hashed_password": hash_password(payload.password)}\n'
                '    return UserPublic(email=email)\n\n\n'
                '@app.post("/auth/login", response_model=Token)\n'
                'def login(form: OAuth2PasswordRequestForm = Depends()):\n'
                '    email = form.username.lower()\n'
                '    user = fake_users_db.get(email)\n'
                '    if not user or not verify_password(form.password, user["hashed_password"]):\n'
                '        raise HTTPException(status_code=401, detail="Invalid credentials")\n'
                '    return Token(access_token=create_access_token(email))\n\n\n'
                '@app.get("/auth/me", response_model=UserPublic)\n'
                'def me(current_user: UserPublic = Depends(get_current_user)):\n'
                '    return current_user\n\n\n'
                '@app.get("/protected")\n'
                'def protected(current_user: UserPublic = Depends(get_current_user)):\n'
                '    return {"message": "protected", "user": current_user.email}\n'
            )
        if self._file_missing_or_lacks(backend_main, ("CryptContext", "/auth/register", "get_current_user", "/protected")):
            patches.append(PatchOperation(
            path=str(backend_main),
            operation="create_file" if not backend_main.exists() else "write_file",
            args={"content": backend_content},
            reason="Create or upgrade FastAPI JWT backend",
        ))

        requirements_content = "fastapi\nuvicorn\npython-jose[cryptography]\npython-multipart\npasslib[bcrypt]\npydantic[email]\n"
        requirements_path = root / "backend" / "requirements.txt"
        if self._file_missing_or_lacks(requirements_path, ("passlib", "python-jose", "python-multipart")):
            patches.append(PatchOperation(
            path=str(root / "backend" / "requirements.txt"),
            operation="create_file" if not requirements_path.exists() else "write_file",
            args={"content": requirements_content},
            reason="Backend dependencies",
        ))

        auth_context_content = (
                '"use client";\n\n'
                'import { createContext, useContext, useEffect, useState, ReactNode } from "react";\n'
                'import { getCurrentUser, loginUser, registerUser } from "@/lib/api";\n\n'
                'type AuthContextType = {\n'
                '  user: string | null;\n'
                '  loading: boolean;\n'
                '  login: (email: string, password: string) => Promise<void>;\n'
                '  register: (email: string, password: string) => Promise<void>;\n'
                '  logout: () => void;\n'
                '};\n\n'
                'const AuthContext = createContext<AuthContextType | undefined>(undefined);\n\n'
                'export function AuthProvider({ children }: { children: ReactNode }) {\n'
                '  const [user, setUser] = useState<string | null>(null);\n'
                '  const [loading, setLoading] = useState(true);\n\n'
                '  useEffect(() => {\n'
                '    const token = localStorage.getItem("token");\n'
                '    if (!token) {\n'
                '      setLoading(false);\n'
                '      return;\n'
                '    }\n'
                '    getCurrentUser(token)\n'
                '      .then((profile) => setUser(profile.email))\n'
                '      .catch(() => localStorage.removeItem("token"))\n'
                '      .finally(() => setLoading(false));\n'
                '  }, []);\n\n'
                '  const login = async (email: string, password: string) => {\n'
                '    const token = await loginUser(email, password);\n'
                '    localStorage.setItem("token", token);\n'
                '    setUser(email);\n'
                '  };\n\n'
                '  const register = async (email: string, password: string) => {\n'
                '    await registerUser(email, password);\n'
                '    await login(email, password);\n'
                '  };\n\n'
                '  const logout = () => {\n'
                '    localStorage.removeItem("token");\n'
                '    setUser(null);\n'
                '  };\n\n'
                '  return (\n'
                '    <AuthContext.Provider value={{ user, loading, login, register, logout }}>\n'
                '      {children}\n'
                '    </AuthContext.Provider>\n'
                '  );\n'
                '}\n\n'
                'export function useAuth() {\n'
                '  const ctx = useContext(AuthContext);\n'
                '  if (!ctx) throw new Error("useAuth must be used within AuthProvider");\n'
                '  return ctx;\n'
                '}\n'
            )
        if self._file_missing_or_lacks(auth_ctx, ("register:", "getCurrentUser", "loading: boolean")):
            patches.append(PatchOperation(
            path=str(auth_ctx),
            operation="create_file" if not auth_ctx.exists() else "write_file",
            args={"content": auth_context_content},
            reason="Create auth context",
        ))

        api_content = (
                'const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";\n\n'
                'export type CurrentUser = { email: string };\n\n'
                'export async function registerUser(email: string, password: string): Promise<CurrentUser> {\n'
                '  const res = await fetch(`${API_BASE}/auth/register`, {\n'
                '    method: "POST",\n'
                '    headers: { "Content-Type": "application/json" },\n'
                '    body: JSON.stringify({ email, password }),\n'
                '  });\n'
                '  if (!res.ok) throw new Error("Registration failed");\n'
                '  return res.json();\n'
                '}\n\n'
                'export async function loginUser(email: string, password: string): Promise<string> {\n'
                '  const body = new URLSearchParams({ username: email, password });\n'
                '  const res = await fetch(`${API_BASE}/auth/login`, {\n'
                '    method: "POST",\n'
                '    headers: { "Content-Type": "application/x-www-form-urlencoded" },\n'
                '    body,\n'
                '  });\n'
                '  if (!res.ok) throw new Error("Login failed");\n'
                '  const data = await res.json();\n'
                '  return data.access_token as string;\n'
                '}\n\n'
                'export async function getCurrentUser(token: string): Promise<CurrentUser> {\n'
                '  const res = await fetch(`${API_BASE}/auth/me`, {\n'
                '    headers: { Authorization: `Bearer ${token}` },\n'
                '  });\n'
                '  if (!res.ok) throw new Error("Not authenticated");\n'
                '  return res.json();\n'
                '}\n'
            )
        if self._file_missing_or_lacks(api_lib, ("registerUser", "getCurrentUser", "Authorization")):
            patches.append(PatchOperation(
            path=str(api_lib),
            operation="create_file" if not api_lib.exists() else "write_file",
            args={"content": api_content},
            reason="API client for JWT login",
        ))

        login_patch = self._login_page_patch()
        if login_patch:
            patches.append(login_patch)

        if layout.exists():
            layout_content = layout.read_text(encoding="utf-8")
            if "AppProviders" not in layout_content:
                patches.extend(self._build_nextjs_rsc_fix(include_auth=True))

        patches.extend(self._build_jwt_cleanup_patches())
        if self._layout_is_corrupted():
            patches.append(PatchOperation(
                path=str(layout),
                operation="write_file",
                args={"content": self._canonical_server_layout()},
                reason="Repair corrupted server layout.tsx",
            ))
            providers = root / "src" / "app" / "providers.tsx"
            if not providers.exists():
                patches.extend(self._build_nextjs_rsc_fix(include_auth=True))
        patches.extend(self._build_navbar_login_patches())

        console.print(f"[cyan]Using JWT scaffold: {len(patches)} deterministic patches[/cyan]")
        return patches

    def _build_missing_home_page_patch(self) -> list[PatchOperation]:
        """Create src/app/page.tsx when root route 404s."""
        page = self.project_root / "src" / "app" / "page.tsx"
        if page.exists():
            return []

        content = (
            'import { Hero } from "@/components/landing/hero";\n'
            'import { Features } from "@/components/landing/features";\n'
            'import { CTA } from "@/components/landing/cta";\n\n'
            'export default function HomePage() {\n'
            '  return (\n'
            '    <>\n'
            '      <Hero />\n'
            '      <Features />\n'
            '      <CTA />\n'
            '    </>\n'
            '  );\n'
            '}\n'
        )
        return [
            PatchOperation(
                path=str(page),
                operation="create_file",
                args={"content": content},
                reason="Create missing root page for / route",
            )
        ]

    def _login_page_patch(self) -> PatchOperation | None:
        login_page = self.project_root / "src" / "app" / "login" / "page.tsx"
        login_page_content = (
            '"use client";\n\n'
            'import { useState } from "react";\n'
            'import { useRouter } from "next/navigation";\n'
            'import { useAuth } from "@/context/auth-context";\n'
            'import { Button } from "@/components/ui/button";\n\n'
            'export default function LoginPage() {\n'
            '  const { login, register } = useAuth();\n'
            '  const router = useRouter();\n'
            '  const [mode, setMode] = useState<"login" | "register">("login");\n'
            '  const [email, setEmail] = useState("");\n'
            '  const [password, setPassword] = useState("");\n'
            '  const [error, setError] = useState("");\n\n'
            '  const onSubmit = async (e: React.FormEvent) => {\n'
            '    e.preventDefault();\n'
            '    setError("");\n'
            '    try {\n'
            '      if (mode === "register") {\n'
            '        await register(email, password);\n'
            '      } else {\n'
            '        await login(email, password);\n'
            '      }\n'
            '      router.push("/dashboard");\n'
            '    } catch {\n'
            '      setError(mode === "register" ? "Registration failed" : "Invalid credentials");\n'
            '    }\n'
            '  };\n\n'
            '  return (\n'
            '    <div className="flex min-h-[60vh] items-center justify-center px-4">\n'
            '      <form onSubmit={onSubmit} className="w-full max-w-md space-y-4 rounded-lg border p-8">\n'
            '        <h1 className="text-2xl font-bold">{mode === "register" ? "Create account" : "Login"}</h1>\n'
            '        {error && <p className="text-sm text-red-500">{error}</p>}\n'
            '        <input className="w-full rounded border px-3 py-2" placeholder="Email"\n'
            '          value={email} onChange={(e) => setEmail(e.target.value)} />\n'
            '        <input type="password" className="w-full rounded border px-3 py-2" placeholder="Password"\n'
            '          value={password} onChange={(e) => setPassword(e.target.value)} />\n'
            '        <Button type="submit" className="w-full">\n'
            '          {mode === "register" ? "Create account" : "Sign in"}\n'
            '        </Button>\n'
            '        <button\n'
            '          type="button"\n'
            '          className="w-full text-sm text-muted-foreground underline-offset-4 hover:underline"\n'
            '          onClick={() => setMode(mode === "register" ? "login" : "register")}\n'
            '        >\n'
            '          {mode === "register" ? "Already have an account? Sign in" : "Need an account? Register"}\n'
            '        </button>\n'
            '      </form>\n'
            '    </div>\n'
            '  );\n'
            '}\n'
        )
        if self._file_missing_or_lacks(login_page, ('"use client"', "register", "setMode")):
            return PatchOperation(
                path=str(login_page),
                operation="create_file" if not login_page.exists() else "write_file",
                args={"content": login_page_content},
                reason="Create or repair login page",
            )
        return None

    def _build_navbar_login_patches(self) -> list[PatchOperation]:
        """Add /login nav link and button only (no recursive JWT call)."""
        patches: list[PatchOperation] = []
        navbar = self.project_root / "src" / "components" / "layout" / "navbar.tsx"

        if navbar.exists():
            nav_content = navbar.read_text(encoding="utf-8")
            if 'href="/login"' not in nav_content:
                patches.append(PatchOperation(
                    path=str(navbar),
                    operation="edit_file",
                    args={
                        "target_text": '{ href: "/dashboard", label: "Dashboard" },',
                        "replacement_text": (
                            '{ href: "/dashboard", label: "Dashboard" },\n'
                            '  { href: "/login", label: "Login" },'
                        ),
                    },
                    reason="Add Login link to navbar",
                ))
            if 'href="/login">Login</Link>' not in nav_content and "Get Started" in nav_content:
                patches.append(PatchOperation(
                    path=str(navbar),
                    operation="insert_after",
                    args={
                        "target_text": '<Link href="/analyze">Get Started</Link>',
                        "content": (
                            '\n          <Button asChild size="sm" variant="outline" className="ml-1 hidden sm:inline-flex">\n'
                            '            <Link href="/login">Login</Link>\n'
                            "          </Button>"
                        ),
                    },
                    reason="Add Login button to navbar",
                ))

        return patches

    def _build_login_navbar_patches(self) -> list[PatchOperation]:
        patches: list[PatchOperation] = []
        login_patch = self._login_page_patch()
        if login_patch:
            patches.append(login_patch)
        patches.extend(self._build_navbar_login_patches())
        return patches

    def _build_nextjs_rsc_fix(self, include_auth: bool = True) -> list[PatchOperation]:
        """Move client providers out of server layout.tsx into providers.tsx."""
        layout = self.project_root / "src" / "app" / "layout.tsx"
        if not layout.exists():
            return []

        content = layout.read_text(encoding="utf-8")
        if "AppProviders" in content and "./providers" in content:
            return []

        needs_fix = any(
            sym in content
            for sym in ("ThemeProvider", "AuthProvider", "CandidatesProvider", "<Navbar", "Navbar />")
        )
        if not needs_fix:
            return []

        providers_path = self.project_root / "src" / "app" / "providers.tsx"
        providers_content = self._providers_tsx_content(include_auth=include_auth)
        new_layout = self._server_layout_content(content)

        patches: list[PatchOperation] = [
            PatchOperation(
                path=str(providers_path),
                operation="create_file" if not providers_path.exists() else "write_file",
                args={"content": providers_content},
                reason="Client-only AppProviders wrapper for RSC boundary",
            ),
            PatchOperation(
                path=str(layout),
                operation="write_file",
                args={"content": new_layout},
                reason="Keep layout.tsx as server component",
            ),
        ]
        return patches

    def _providers_tsx_content(self, include_auth: bool = True) -> str:
        auth_import = 'import { AuthProvider } from "@/context/auth-context";\n' if include_auth else ""
        auth_open = "      <AuthProvider>\n" if include_auth else ""
        auth_close = "      </AuthProvider>\n" if include_auth else ""
        return (
            '"use client";\n\n'
            'import { ThemeProvider } from "@/components/theme-provider";\n'
            f'{auth_import}'
            'import { CandidatesProvider } from "@/context/candidates-context";\n'
            'import { Navbar } from "@/components/layout/navbar";\n'
            'import { Footer } from "@/components/layout/footer";\n\n'
            'export function AppProviders({ children }: { children: React.ReactNode }) {\n'
            '  return (\n'
            '    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>\n'
            f'{auth_open}'
            '        <CandidatesProvider>\n'
            '          <Navbar />\n'
            '          <main className="flex-1">{children}</main>\n'
            '          <Footer />\n'
            '        </CandidatesProvider>\n'
            f'{auth_close}'
            '    </ThemeProvider>\n'
            '  );\n'
            '}\n'
        )

    def _server_layout_content(self, old_layout: str) -> str:
        """Strip client imports/providers from layout; keep metadata and fonts."""
        lines = old_layout.splitlines()
        kept: list[str] = []
        skip_prefixes = (
            'import { ThemeProvider',
            'import { AuthProvider',
            'import { CandidatesProvider',
            'import { Navbar',
            'import { Footer',
        )
        for line in lines:
            if any(line.strip().startswith(p) for p in skip_prefixes):
                continue
            kept.append(line)

        body = "\n".join(kept)
        if 'import { AppProviders } from "./providers"' not in body:
            body = re.sub(
                r'(import type \{ Metadata \} from "next";)',
                r'\1\nimport { AppProviders } from "./providers";',
                body,
                count=1,
            )
            if 'import { AppProviders } from "./providers"' not in body:
                body = 'import { AppProviders } from "./providers";\n' + body

        body = re.sub(
            r"<ThemeProvider[\s\S]*?</ThemeProvider>",
            "<AppProviders>{children}</AppProviders>",
            body,
            count=1,
        )
        if "<AppProviders>" not in body:
            body = re.sub(
                r"(<body[^>]*>)\s*([\s\S]*?)\s*(</body>)",
                r"\1\n        <AppProviders>{children}</AppProviders>\n      \3",
                body,
                count=1,
            )
        return body

    def _find_files_with_symbol(self, symbol: str) -> list[str]:
        result = FileTool.find_references(str(self.project_root), symbol)
        files: list[str] = []
        if result["status"] == "success":
            seen: set[str] = set()
            for ref in result.get("references", []):
                f = ref.get("file", "")
                if f and f not in seen:
                    seen.add(f)
                    files.append(normalize_path_key(f, self.project_root))
        return files

    async def _apply_with_reflection(self, patch: PatchOperation) -> bool:
        path = normalize_path_key(patch.path, self.project_root)
        patch.path = path

        if patch.operation == "create_file":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            if Path(path).exists():
                patch.operation = "write_file"
            else:
                return FileTool.create_file(path, patch.args.get("content", ""))["status"] == "success"

        if path not in self._files_read and Path(path).exists():
            result = FileTool.read_file(path)
            if result["status"] == "success":
                self._files_read.add(path)
                self.file_index.add(path, result.get("raw", ""))

        errors_encountered = []

        def apply() -> bool:
            return self._execute_patch(patch).get("status") == "success"

        def verify() -> str:
            return Verifier.verify_file(path, project_root=self.project_root) if Path(path).exists() else "FAIL: File deleted or missing"

        def rollback() -> None:
            if FileEditor.has_backup(path):
                FileEditor.rollback(path)
            elif patch.operation == "create_file" and Path(path).exists():
                Path(path).unlink(missing_ok=True)
                
        async def regenerate(error_msg: str) -> bool:
            errors_encountered.append(error_msg)
            # Re-run PatchGenerator with explicit error context
            console.print(f"[yellow]Regenerating patch due to error: {error_msg}[/yellow]")
            feedback_request = (
                f"The previous patch for {patch.path} failed verification.\n"
                f"Error traceback:\n{error_msg}\n\n"
                "INTELLIGENT DEBUGGING REQUIRED:\n"
                "1. Read the traceback carefully.\n"
                "2. Locate the root cause of the error.\n"
                "3. Generate a minimal, highly-confident patch to fix ONLY this error.\n"
                "Do not rewrite unrelated code."
            )
            
            dummy_plan = EditPlan(
                goal=feedback_request,
                files_to_read=[patch.path],
                files_to_edit=[patch.path],
                dependencies=[],
                risks=["Breaking existing functionality if the fix is not minimal"],
                verification_strategy="Run compiler/linter again to verify the fix",
                symbols_to_modify=[],
                plan_steps=[f"Fix verification error in {patch.path}"]
            )
            
            new_patches = await self.patch_generator.generate_patches(
                user_request=feedback_request,
                edit_plan=dummy_plan,
                project_root=self.project_root,
                file_index=self.file_index,
                project_structure=self._project_structure(),
                rag_context=self.rag_context,
            )
            
            if not new_patches:
                return False
                
            # Update the current patch reference with the newly generated one
            new_patch = new_patches[0]
            patch.operation = new_patch.operation
            patch.args = new_patch.args
            patch.reason = new_patch.reason
            
            return True

        success = await self.reflection.execute_with_reflection(apply, verify, rollback, regenerate)
        if success and errors_encountered:
            fix_desc = patch.reason or "Applied regenerated patch successfully"
            self.experience_memory.record_bug_fix(errors_encountered[-1], patch.path, fix_desc)
        return success

    def _execute_patch(self, patch: PatchOperation) -> dict:
        operation, args = normalize_patch_args(patch.operation, patch.args)
        args = {"path": patch.path, **args}
        dispatch = {
            "create_file": FileTool.create_file,
            "write_file": FileTool.write_file,
            "edit_file": FileTool.edit_file,
            "replace_lines": FileTool.replace_lines,
            "append_file": FileTool.append_file,
            "insert_before": FileTool.insert_before,
            "insert_after": FileTool.insert_after,
            "replace_regex": FileTool.replace_regex,
            "delete_block": FileTool.delete_block,
            "rename_symbol": FileTool.rename_symbol,
            "delete_file": FileTool.delete_file,
        }
        fn = dispatch.get(operation)
        if not fn:
            return {"status": "error", "message": f"Unknown operation: {operation}"}
        try:
            return fn(**args)
        except TypeError as exc:
            return {
                "status": "error",
                "message": f"{operation} arg error: {exc}. Args keys: {list(args.keys())}",
            }

    def _build_summary(self, request: str, files: list[str]) -> str:
        lines = [f"Completed: {request}", f"Modified {len(files)} file(s):"]
        for f in files:
            lines.append(f"  - {to_rel_path(f, self.project_root)}")
        return "\n".join(lines)


async def run_symbol_rename(project_root: str | Path, old_name: str, new_name: str) -> EditResult:
    root = Path(project_root).resolve()
    refs = FileTool.find_references(str(root), old_name)
    if refs["status"] != "success":
        return EditResult(success=False, message="Could not find references")

    files_modified: list[str] = []
    reflection = ReflectionEngine(max_retries=CodingWorkflow.MAX_RETRIES)
    target_files = {
        normalize_path_key(ref["file"], root)
        for ref in refs.get("references", [])
        if ref.get("file")
    }

    for fpath in sorted(target_files):
        FileTool.read_file(fpath)

        def apply(fp=fpath) -> bool:
            return FileTool.rename_symbol(fp, old_name, new_name)["status"] == "success"

        def verify(fp=fpath) -> str:
            return Verifier.verify_file(fp, project_root=root)

        def rollback(fp=fpath) -> None:
            FileEditor.rollback(fp)

        if await reflection.execute_with_reflection_sync(apply, verify, rollback):
            files_modified.append(fpath)
        else:
            return EditResult(
                success=False,
                message=f"Rename failed on {to_rel_path(fpath, root)}",
                files_modified=files_modified,
            )

    return EditResult(
        success=True,
        message=f"Renamed {old_name} → {new_name} in {len(files_modified)} files",
        files_modified=files_modified,
    )
