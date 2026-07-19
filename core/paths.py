from pathlib import Path


def get_desktop_path() -> Path:
    """Return the user's Desktop directory (OneDrive Desktop on Windows if present)."""
    home = Path.home()
    onedrive_desktop = home / "OneDrive" / "Desktop"
    if onedrive_desktop.exists():
        return onedrive_desktop
    desktop = home / "Desktop"
    if desktop.exists():
        return desktop
    return home


def resolve_project_path(path: str | Path, project_root: Path | str | None = None) -> Path:
    """Resolve a path relative to project_root or as absolute."""
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    if project_root:
        return (Path(project_root) / p).resolve()
    return p.resolve()


def to_rel_path(path: str | Path, project_root: Path | str) -> str:
    """Project-relative path with forward slashes."""
    try:
        return str(Path(path).resolve().relative_to(Path(project_root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def normalize_path_key(path: str | Path, project_root: Path | str | None = None) -> str:
    """Canonical absolute path string for dictionaries and comparisons."""
    return str(resolve_project_path(path, project_root))


def is_path_in_project(path: str | Path, project_root: Path | str) -> bool:
    """True if path resolves inside project_root."""
    try:
        Path(path).resolve().relative_to(Path(project_root).resolve())
        return True
    except ValueError:
        return False


def sanitize_llm_path(path: str, project_root: Path | str) -> str | None:
    """
    Convert LLM path output to a valid project-relative or absolute path.
    Returns None if the path is clearly hallucinated (foreign absolute paths).
    """
    if not path or not str(path).strip():
        return None

    raw = str(path).strip().replace("\\", "/")
    root = Path(project_root).resolve()
    proj_norm = str(root).replace("\\", "/").lower()

    foreign_markers = ("/home/", "/users/", "/runner/work/", "ai-recruiting-platform")
    if any(m in raw.lower() for m in foreign_markers) and proj_norm not in raw.lower():
        return None

    # Leading slash on relative path -> strip
    if raw.startswith("/") and not raw.startswith("//"):
        candidate = raw.lstrip("/")
        if (root / candidate).exists() or candidate.startswith(("src/", "backend/", "app/")):
            raw = candidate

    # Next.js App Router: prefer src/app over legacy app/
    if raw.startswith("app/") and not (root / raw).exists():
        src_candidate = "src/" + raw
        if (root / src_candidate).exists():
            raw = src_candidate

    resolved = resolve_project_path(raw, root)
    if is_path_in_project(resolved, root):
        return str(resolved)
    return None


class FileContentIndex:
    """Maps file paths (absolute or relative) to raw file content."""

    def __init__(self, project_root: Path | str) -> None:
        self.root = Path(project_root).resolve()
        self._content: dict[str, str] = {}

    def add(self, path: str | Path, content: str) -> None:
        key = normalize_path_key(path, self.root)
        self._content[key] = content

    def get(self, path: str | Path) -> str | None:
        key = normalize_path_key(path, self.root)
        if key in self._content:
            return self._content[key]
        rel = to_rel_path(path, self.root) if path else ""
        for abs_key, content in self._content.items():
            if to_rel_path(abs_key, self.root) == rel:
                return content
        return None

    def items_for_prompt(self, paths: list[str]) -> dict[str, str]:
        """Return {relative_path: content} for prompt injection."""
        out: dict[str, str] = {}
        for p in paths:
            content = self.get(p)
            if content is not None:
                rel = to_rel_path(p, self.root) if not p.startswith("(") else p
                out[rel] = content
        return out

    def all_for_prompt(self) -> dict[str, str]:
        return {to_rel_path(k, self.root): v for k, v in self._content.items()}
