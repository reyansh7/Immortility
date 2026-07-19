"""Indexing hygiene: noise directories, secret files, and chunk-level secret scanning."""

from __future__ import annotations

import re
from pathlib import Path

# High-noise directories — never walk / index
DENY_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        ".next",
        "dist",
        "build",
        "coverage",
        "venv",
        ".venv",
        "__pycache__",
        ".tox",
        ".eggs",
        ".cache",
        ".nuxt",
        ".turbo",
        ".parcel-cache",
        "target",
        "bin",
        "obj",
        ".gradle",
        ".idea",
        ".vscode",
        ".vs",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".vector_db",
        "vector_db",
        ".immortility",
        ".checkpoints",
        "logs",
        "graphify-out",
    }
)

# Sensitive filenames (exact basename match, case-insensitive)
DENY_FILENAMES: frozenset[str] = frozenset(
    {
        ".env",
        "credentials.json",
        "service_account.json",
        "id_rsa",
        "id_ed25519",
        "id_dsa",
        "id_ecdsa",
    }
)

# Sensitive extensions
DENY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".jks",
        ".keystore",
    }
)

# Chunk-level secret patterns — matching chunks are never embedded
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)OPENAI[_-]?API[_-]?KEY\s*[:=]\s*\S+"),
    re.compile(r"(?i)(API[_-]?KEY|SECRET[_-]?KEY|ACCESS[_-]?TOKEN)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-_=.]{20,}"),
    re.compile(r"-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)aws[_-]?secret[_-]?access[_-]?key\s*[:=]\s*\S+"),
)


def is_denied_dirname(name: str) -> bool:
    return name in DENY_DIRS or name.startswith(".venv")


def is_denied_file(path: str | Path) -> bool:
    """Return True if this file must never be indexed."""
    p = Path(path)
    name = p.name
    lower = name.lower()

    if lower in {n.lower() for n in DENY_FILENAMES}:
        return True
    # .env and .env.* (including .env.local, .env.development)
    if lower == ".env" or lower.startswith(".env."):
        return True
    if p.suffix.lower() in DENY_EXTENSIONS:
        return True
    return False


def path_has_denied_dir(path: str | Path) -> bool:
    """True if any path component is a denied directory."""
    return any(is_denied_dirname(part) for part in Path(path).parts)


def should_index_path(path: str | Path) -> bool:
    """Combined gate: not under a denied directory and not a denied file."""
    p = Path(path)
    if path_has_denied_dir(p):
        return False
    if is_denied_file(p):
        return False
    return True


def chunk_contains_secret(text: str) -> bool:
    """Return True if text matches secret patterns and must not be embedded."""
    if not text:
        return False
    return any(pat.search(text) for pat in SECRET_PATTERNS)
