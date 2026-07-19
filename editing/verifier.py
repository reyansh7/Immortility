import logging
import json
import subprocess
from pathlib import Path

from editing.patch_validator import PatchValidator

logger = logging.getLogger(__name__)


class Verifier:
    """Verification engine — syntax checks and compiler/linter checks after edits."""

    @staticmethod
    def verify_file(file_path: str, project_root: str | Path | None = None) -> str:
        path = Path(file_path)
        if not path.exists():
            return "FAIL: File does not exist"
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return f"FAIL: Could not read file. {e}"
        
        # 1. Static Syntax Checks (Fast)
        valid, msg = PatchValidator.validate_syntax(content, file_path)
        if not valid:
            logger.error("Verification failed for %s: %s", file_path, msg)
            return f"FAIL: {msg}"
            
        # 2. Dynamic Project Checks (Compiler / Linter)
        if project_root:
            root = Path(project_root).resolve()
            ext = path.suffix.lower()
            
            try:
                if ext == ".py":
                    # Try py_compile
                    subprocess.run(["python", "-m", "py_compile", str(path)], cwd=root, check=True, capture_output=True, text=True)
                    # Try flake8 if available
                    res = subprocess.run(["flake8", str(path)], cwd=root, capture_output=True, text=True)
                    if res.returncode != 0 and "not found" not in res.stderr.lower() and "not recognized" not in res.stderr.lower():
                        # Only fail if flake8 is installed and actually found errors
                        if res.stdout.strip():
                            logger.warning("Flake8 warnings for %s:\n%s", path.name, res.stdout)
                            # We don't strictly fail on flake8 to avoid endless loops on style, 
                            # but we could return it as a hint if it's an indentation error.
                            if "IndentationError" in res.stdout or "SyntaxError" in res.stdout or "E999" in res.stdout:
                                return f"FAIL: Flake8 syntax error: {res.stdout}"

                elif ext in (".ts", ".tsx"):
                    # Check if tsc is available in node_modules or globally
                    # We run tsc --noEmit to typecheck the whole project (or just rely on eslint)
                    # Since tsc is slow for the whole project, we might just try eslint on the file
                    res = subprocess.run(["npx", "eslint", str(path)], cwd=root, capture_output=True, text=True)
                    if res.returncode != 0 and "not found" not in res.stderr.lower() and "could not determine executable" not in res.stderr.lower():
                        if "Parsing error" in res.stdout or "Syntax error" in res.stdout:
                            return f"FAIL: ESLint parsing error: {res.stdout}"

            except subprocess.CalledProcessError as e:
                # py_compile failed
                if ext == ".py":
                    return f"FAIL: Python compilation error:\n{e.stderr}"
            except Exception as e:
                logger.debug("Project verifier check skipped or failed silently: %s", e)

        return "PASS"

    @staticmethod
    def verify_nextjs_build(project_root: str | Path) -> tuple[bool, str]:
        """Run npm run build for Next.js projects. Returns (ok, message)."""
        root = Path(project_root).resolve()
        pkg = root / "package.json"
        if not pkg.exists():
            return True, "skip: no package.json"

        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return True, "skip: invalid package.json"

        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        if "next" not in deps:
            return True, "skip: not a Next.js project"

        try:
            result = subprocess.run(
                "npm run build",
                cwd=root,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return False, "npm run build timed out after 300s"
        except OSError as exc:
            return True, f"skip: build not run ({exc})"

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return False, output[-4000:]
        return True, "npm run build passed"

    @staticmethod
    def verify_pytest(project_root: str | Path) -> tuple[bool, str]:
        """Backward-compatible alias for repository-aware test runner."""
        return Verifier.verify_project_tests(project_root)

    @staticmethod
    def verify_project_tests(project_root: str | Path) -> tuple[bool, str]:
        """Run the test command appropriate for the detected repository type."""
        from core.repo_detector import RepositoryDetector

        root = Path(project_root).resolve()
        profile = RepositoryDetector.detect(root)
        if not profile.test_command:
            return True, f"skip: no test command ({profile.reason})"

        try:
            result = subprocess.run(
                profile.test_command,
                cwd=str(profile.root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"{' '.join(profile.test_command)} timed out after 180s"
        except FileNotFoundError as exc:
            return True, f"skip: test runner missing ({exc})"
        except OSError as exc:
            return True, f"skip: tests not run ({exc})"

        output = (result.stdout or "") + (result.stderr or "")
        label = " ".join(profile.test_command)
        if result.returncode != 0:
            return False, f"{label} failed ({profile.kind}):\n{output[-3000:]}"
        return True, f"{label} passed ({profile.kind})"
