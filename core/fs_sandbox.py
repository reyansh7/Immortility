"""Filesystem sandbox for Immortility file and command tools.

Default roots are this repo and the user's Desktop — not the whole home
directory. Widen later with IMMORTILITY_FS_ROOTS after sandbox tests pass.
"""

from __future__ import annotations

import os
from pathlib import Path

DENIED_DIR_NAMES = frozenset({
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
})


def _home_sensitive_prefixes() -> list[Path]:
    home = Path.home()
    appdata = home / "AppData"
    return [
        home / ".ssh",
        home / ".gnupg",
        home / ".aws",
        home / ".azure",
        home / ".kube",
        home / ".docker",
        appdata / "Roaming",
        appdata / "LocalLow",
        appdata / "Local" / "Microsoft" / "Credentials",
    ]

DENIED_FILE_NAMES = frozenset({
    ".env",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    "ntuser.dat",
})

_cached_roots: list[Path] | None = None


def reset_fs_sandbox() -> None:
    global _cached_roots
    _cached_roots = None


def _split_roots(raw: str) -> list[Path]:
    parts: list[Path] = []
    for chunk in raw.replace(";", os.pathsep).split(os.pathsep):
        text = chunk.strip().strip('"')
        if not text:
            continue
        try:
            parts.append(Path(text).expanduser().resolve())
        except OSError:
            continue
    return parts


def default_roots() -> list[Path]:
    from core.paths import get_desktop_path
    from core.repo_paths import get_repo_root

    roots: list[Path] = []
    for candidate in (get_repo_root(), get_desktop_path()):
        try:
            resolved = Path(candidate).resolve()
        except OSError:
            continue
        if resolved not in roots:
            roots.append(resolved)
    return roots


def allowed_roots() -> list[Path]:
    global _cached_roots
    if _cached_roots is not None:
        return list(_cached_roots)
    raw = (os.environ.get("IMMORTILITY_FS_ROOTS") or "").strip()
    roots = _split_roots(raw) if raw else default_roots()
    _cached_roots = roots
    return list(roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _denied_reason(path: Path) -> str | None:
    parts = [p.lower() for p in path.parts]
    for part in parts:
        if part in DENIED_DIR_NAMES:
            return f"Blocked sensitive path: {path}"
    name = path.name.lower()
    if name in DENIED_FILE_NAMES or name.startswith(".env"):
        return f"Blocked sensitive file: {path}"
    for prefix in _home_sensitive_prefixes():
        try:
            if path.resolve() == prefix.resolve() or _is_relative_to(path, prefix.resolve()):
                return f"Blocked sensitive path: {path}"
        except OSError:
            continue
    return None


def is_inside_sandbox(path: str | Path) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return False
    return any(_is_relative_to(resolved, root) for root in allowed_roots())


def check_path(path: str | Path | None, *, write: bool = False) -> tuple[bool, Path | None, str]:
    """Return (ok, resolved, error). Empty path is invalid."""
    if path is None or not str(path).strip():
        return False, None, "Path is required."
    try:
        resolved = Path(str(path)).expanduser().resolve()
    except OSError as exc:
        return False, None, f"Invalid path: {exc}"

    denied = _denied_reason(resolved)
    if denied:
        return False, resolved, denied

    if not is_inside_sandbox(resolved):
        roots = ", ".join(str(r) for r in allowed_roots()) or "(none)"
        kind = "write" if write else "read"
        return False, resolved, f"Sandbox blocked {kind} outside allowed roots: {resolved}. Allowed: {roots}"
    return True, resolved, ""


def default_scan_root() -> Path:
    roots = allowed_roots()
    if not roots:
        raise RuntimeError("No filesystem roots configured.")
    return roots[0]


def scan_storage(path: str = "", limit: int = 20) -> dict:
    """Read-only size report of the largest files and folders inside the sandbox."""
    ok, root, err = check_path(path or default_scan_root(), write=False)
    if not ok or root is None:
        return {"status": "error", "message": err}
    if not root.exists():
        return {"status": "error", "message": f"Path not found: {root}"}

    limit = max(1, min(int(limit), 100))
    files: list[dict] = []
    dirs: dict[str, int] = {}
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}

    if root.is_file():
        size = root.stat().st_size
        return {
            "status": "success",
            "root": str(root),
            "total_bytes": size,
            "largest_files": [{"path": str(root), "size_bytes": size}],
            "largest_dirs": [],
        }

    total = 0
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in skip and d.lower() not in DENIED_DIR_NAMES
        ]
        for name in filenames:
            fpath = Path(current) / name
            if _denied_reason(fpath):
                continue
            try:
                size = fpath.stat().st_size
            except OSError:
                continue
            total += size
            files.append({"path": str(fpath), "size_bytes": size})
            parent = str(fpath.parent)
            dirs[parent] = dirs.get(parent, 0) + size

    files.sort(key=lambda row: row["size_bytes"], reverse=True)
    dir_rows = [
        {"path": p, "size_bytes": s} for p, s in dirs.items()
    ]
    dir_rows.sort(key=lambda row: row["size_bytes"], reverse=True)
    return {
        "status": "success",
        "root": str(root),
        "total_bytes": total,
        "largest_files": files[:limit],
        "largest_dirs": dir_rows[:limit],
    }
