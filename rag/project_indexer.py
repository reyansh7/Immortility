"""Project-level indexer with automatic framework / language detection.

Walks a project directory, indexes all supported source files, and
auto-detects the project's technology stack by inspecting manifest
files (``package.json``, ``requirements.txt``, ``pyproject.toml``, etc.).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from rag.indexer import Indexer
from rag.security_filters import is_denied_dirname, should_index_path

logger = logging.getLogger(__name__)

# ── File filters ────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Web
        ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md",
        ".yaml", ".yml", ".toml", ".html", ".htm", ".css", ".scss",
        ".sass", ".less", ".svg",
        # Systems
        ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
        ".cs", ".rb", ".php", ".swift", ".kt", ".kts",
        # Data / Config (secrets like .env excluded via security_filters)
        ".sql", ".graphql", ".gql", ".proto", ".prisma",
        ".xml", ".ini", ".cfg", ".conf",
        # Scripting
        ".sh", ".bash", ".bat", ".ps1", ".lua", ".r",
        ".dart", ".ex", ".exs", ".erl",
        # Docs
        ".txt", ".rst", ".mdx",
    }
)
SUPPORTED_NAMES: frozenset[str] = frozenset(
    {
        "Dockerfile", "Makefile", ".gitignore", ".dockerignore",
        "Procfile", "Gemfile", "Rakefile", "Vagrantfile",
        "CMakeLists.txt", "go.mod", "go.sum",
    }
)

SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "env",
        "build",
        "dist",
        ".cache",
        "__pycache__",
        ".next",
        ".nuxt",
        "coverage",
        ".tox",
        ".eggs",
        "*.egg-info",
        "vector_db",
        ".vector_db",
        ".pytest_tmp",
        ".pytest_cache",
        ".ruff_cache",
        # IDE / editor
        ".idea",
        ".vscode",
        ".vs",
        # Build output
        "target",
        "bin",
        "obj",
        ".gradle",
        ".cargo",
        # Project-specific
        ".immortility",
        ".checkpoints",
        ".audit_tmp",
        "logs",
        ".DS_Store",
        ".mypy_cache",
        ".parcel-cache",
        ".turbo",
        "graphify-out",
    }
)


# ── Data model ──────────────────────────────────────────────────────


@dataclass
class ProjectInfo:
    """Detected project metadata."""

    name: str
    path: str
    language: str = ""
    framework: str = ""
    database: str = ""
    package_manager: str = ""
    project_type: str = ""
    dependencies: list[str] = field(default_factory=list)
    important_files: list[str] = field(default_factory=list)
    folder_structure: list[str] = field(default_factory=list)
    total_files: int = 0
    total_chunks: int = 0
    index_time_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "name": self.name,
            "path": self.path,
            "language": self.language,
            "framework": self.framework,
            "database": self.database,
            "package_manager": self.package_manager,
            "project_type": self.project_type,
            "dependencies": self.dependencies,
            "important_files": self.important_files,
            "folder_structure": self.folder_structure,
            "total_files": self.total_files,
            "total_chunks": self.total_chunks,
            "index_time_seconds": self.index_time_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectInfo":
        """Deserialise from a plain dictionary."""
        return cls(
            name=data.get("name", ""),
            path=data.get("path", ""),
            language=data.get("language", ""),
            framework=data.get("framework", ""),
            database=data.get("database", ""),
            package_manager=data.get("package_manager", ""),
            project_type=data.get("project_type", ""),
            dependencies=data.get("dependencies", []),
            important_files=data.get("important_files", []),
            folder_structure=data.get("folder_structure", []),
            total_files=data.get("total_files", 0),
            total_chunks=data.get("total_chunks", 0),
            index_time_seconds=data.get("index_time_seconds", 0.0),
        )

    def summary(self) -> str:
        """Human-readable project summary for prompt injection."""
        parts = [f"Project: {self.name}"]
        if self.language:
            parts.append(f"Language: {self.language}")
        if self.framework:
            parts.append(f"Framework: {self.framework}")
        if self.database:
            parts.append(f"Database: {self.database}")
        if self.package_manager:
            parts.append(f"Package Manager: {self.package_manager}")
        if self.project_type:
            parts.append(f"Type: {self.project_type}")
        if self.important_files:
            parts.append(f"Key Files: {', '.join(self.important_files[:10])}")
        parts.append(f"Files: {self.total_files}  Chunks: {self.total_chunks}")
        return "\n".join(parts)


# ── Project indexer ─────────────────────────────────────────────────


class ProjectIndexer:
    """Walks a project tree, indexes all source files, and detects the tech stack.

    Parameters:
        indexer: File-level indexer (handles chunking + embedding + storage).
    """

    def __init__(self, indexer: Indexer | None = None) -> None:
        self._indexer = indexer or Indexer()

    def index_project(
        self,
        project_path: str | Path,
        project_name: str | None = None,
        *,
        on_progress: Any | None = None,
    ) -> ProjectInfo:
        """Index an entire project directory.

        Args:
            project_path: Root of the project.
            project_name: Display name (defaults to directory name).
            on_progress: Optional ``callable(done, total, path, chunks_added)``.

        Returns:
            A ``ProjectInfo`` with detected metadata and indexing stats.
        """
        root = Path(project_path).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Project path not found: {root}")

        name = project_name or root.name
        logger.info("Indexing project: %s (%s)", name, root)
        t0 = time.perf_counter()

        # Collect files
        files = list(self._walk(root))
        logger.info("Found %d indexable files.", len(files))
        if on_progress:
            try:
                on_progress(0, len(files), root, 0)
            except Exception:
                pass

        # Index each file
        total_chunks = 0
        for i, fp in enumerate(files, 1):
            added = 0
            try:
                added = self._indexer.index_file(fp, name)
                total_chunks += added
            except Exception as exc:
                logger.error("Failed to index %s: %s", fp, exc)
            if on_progress:
                try:
                    on_progress(i, len(files), fp, added)
                except Exception:
                    pass

        elapsed = time.perf_counter() - t0

        # Detect project metadata
        info = self._detect_project(root, name)
        info.total_files = len(files)
        info.total_chunks = total_chunks
        info.index_time_seconds = round(elapsed, 2)
        info.important_files = self._find_important_files(root)
        info.folder_structure = self._get_folder_structure(root)

        logger.info(
            "Project indexed: %d files, %d chunks in %.1fs",
            len(files),
            total_chunks,
            elapsed,
        )
        return info

    # ── Directory walking ───────────────────────────────────────────

    def _walk(self, root: Path) -> list[Path]:
        """Recursively collect indexable files, respecting skip/deny patterns."""
        files: list[Path] = []
        try:
            entries = sorted(root.iterdir())
        except PermissionError:
            return files

        for item in entries:
            if item.is_dir():
                if (
                    item.name in SKIP_DIRS
                    or is_denied_dirname(item.name)
                    or item.name.startswith(".venv")
                ):
                    continue
                files.extend(self._walk(item))
            elif item.is_file():
                if item.name in SUPPORTED_NAMES or item.suffix.lower() in SUPPORTED_EXTENSIONS:
                    if should_index_path(item):
                        files.append(item)
        return files

    # ── Auto-detection ──────────────────────────────────────────────

    def _detect_project(self, root: Path, name: str) -> ProjectInfo:
        """Inspect manifest files to determine the project's tech stack."""
        info = ProjectInfo(name=name, path=str(root))
        languages: list[str] = []
        frameworks: list[str] = []
        databases: list[str] = []
        deps: list[str] = []

        # ── Python ──────────────────────────────────────────────────
        req = root / "requirements.txt"
        if req.exists():
            info.package_manager = "pip"
            languages.append("Python")
            try:
                content = req.read_text(encoding="utf-8", errors="replace").lower()
                deps.extend(self._parse_requirements(content))
                frameworks.extend(self._detect_python_frameworks(content))
                databases.extend(self._detect_databases(content))
            except OSError as e:
                logger.warning("Skipped unreadable file: %s (%s)", req, e)

        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            languages.append("Python")
            if not info.package_manager:
                info.package_manager = "pip"
            try:
                content = pyproject.read_text(encoding="utf-8", errors="replace").lower()
                if "poetry" in content:
                    info.package_manager = "poetry"
                frameworks.extend(self._detect_python_frameworks(content))
            except OSError as e:
                logger.warning("Skipped unreadable file: %s (%s)", pyproject, e)

        # ── Node.js ─────────────────────────────────────────────────
        pkg_json = root / "package.json"
        if pkg_json.exists():
            languages.append("JavaScript")
            info.package_manager = "npm"
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                all_deps = {}
                all_deps.update(pkg.get("dependencies", {}))
                all_deps.update(pkg.get("devDependencies", {}))
                deps.extend(list(all_deps.keys())[:50])

                if "react" in all_deps:
                    frameworks.append("React")
                if "next" in all_deps:
                    frameworks.append("Next.js")
                if "vue" in all_deps:
                    frameworks.append("Vue")
                if "express" in all_deps:
                    frameworks.append("Express")
                if "angular" in all_deps or "@angular/core" in all_deps:
                    frameworks.append("Angular")
                if "svelte" in all_deps:
                    frameworks.append("Svelte")
                if "prisma" in all_deps or "@prisma/client" in all_deps:
                    databases.append("Prisma")
                if "mongoose" in all_deps:
                    databases.append("MongoDB")
                if "pg" in all_deps:
                    databases.append("PostgreSQL")

                # TypeScript detection
                if "typescript" in all_deps:
                    languages.append("TypeScript")

                if (root / "yarn.lock").exists():
                    info.package_manager = "yarn"
                elif (root / "pnpm-lock.yaml").exists():
                    info.package_manager = "pnpm"
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Skipped unreadable or invalid file: %s (%s)", pkg_json, e)

        # ── Rust ────────────────────────────────────────────────────
        if (root / "Cargo.toml").exists():
            languages.append("Rust")
            info.package_manager = "cargo"

        # ── Determine primary language ──────────────────────────────
        if languages:
            info.language = languages[0]
        if frameworks:
            info.framework = ", ".join(dict.fromkeys(frameworks))
        if databases:
            info.database = ", ".join(dict.fromkeys(databases))
        info.dependencies = deps[:50]

        # ── Project type heuristic ──────────────────────────────────
        info.project_type = self._detect_project_type(root, info)

        return info

    @staticmethod
    def _parse_requirements(content: str) -> list[str]:
        """Parse package names from requirements.txt content."""
        deps: list[str] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            name = line.split(">=")[0].split("==")[0].split("<")[0].split(">")[0].strip()
            if name:
                deps.append(name)
        return deps

    @staticmethod
    def _detect_python_frameworks(content: str) -> list[str]:
        frameworks: list[str] = []
        mapping = {
            "fastapi": "FastAPI",
            "django": "Django",
            "flask": "Flask",
            "streamlit": "Streamlit",
            "gradio": "Gradio",
            "pytorch": "PyTorch",
            "torch": "PyTorch",
            "tensorflow": "TensorFlow",
            "ultralytics": "YOLO/Ultralytics",
            "opencv": "OpenCV",
            "cv2": "OpenCV",
        }
        for key, name in mapping.items():
            if key in content:
                frameworks.append(name)
        return frameworks

    @staticmethod
    def _detect_databases(content: str) -> list[str]:
        databases: list[str] = []
        mapping = {
            "sqlalchemy": "SQLAlchemy",
            "psycopg": "PostgreSQL",
            "pymongo": "MongoDB",
            "redis": "Redis",
            "sqlite": "SQLite",
            "mysql": "MySQL",
        }
        for key, name in mapping.items():
            if key in content:
                databases.append(name)
        return databases

    @staticmethod
    def _detect_project_type(root: Path, info: ProjectInfo) -> str:
        """Heuristic: what kind of project is this?"""
        fw = (info.framework or "").lower()
        if "next" in fw or "react" in fw or "vue" in fw or "angular" in fw:
            return "web-frontend"
        if "fastapi" in fw or "django" in fw or "flask" in fw or "express" in fw:
            return "web-backend"
        if "yolo" in fw or "pytorch" in fw or "tensorflow" in fw:
            return "ml-project"
        if (root / "Dockerfile").exists():
            return "containerised-app"
        if info.language == "Python":
            return "python-project"
        if info.language in ("JavaScript", "TypeScript"):
            return "node-project"
        return "unknown"

    @staticmethod
    def _find_important_files(root: Path) -> list[str]:
        """Identify key files in the project root."""
        important: list[str] = []
        candidates = [
            "main.py", "app.py", "manage.py", "server.py", "index.py",
            "index.js", "index.ts", "app.js", "app.ts", "server.js",
            "package.json", "requirements.txt", "pyproject.toml",
            "Dockerfile", "docker-compose.yml", "README.md",
            "Makefile",
        ]
        for name in candidates:
            if (root / name).exists():
                important.append(name)
        return important

    @staticmethod
    def _get_folder_structure(root: Path, max_depth: int = 2) -> list[str]:
        """Return a shallow folder structure (for project profile display)."""
        structure: list[str] = []

        def _recurse(path: Path, depth: int, prefix: str = "") -> None:
            if depth > max_depth:
                return
            try:
                entries = sorted(path.iterdir())
            except PermissionError:
                return
            dirs = [e for e in entries if e.is_dir() and e.name not in SKIP_DIRS and not e.name.startswith(".venv")]
            for d in dirs[:20]:
                structure.append(f"{prefix}{d.name}/")
                _recurse(d, depth + 1, prefix + "  ")

        _recurse(root, 0)
        return structure[:50]
