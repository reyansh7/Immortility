"""Git primitives — Tool Kernel wrappers over the single command engine.

Never spawn subprocesses here. All git argv goes through ``CommandTool.run_argv``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from core.repo_paths import get_repo_root
from tools.command_tool import CommandTool, classify_argv, resolve_project_cwd

CLASS_READONLY = "readonly"
CLASS_MUTATING = "mutating"
CLASS_DESTRUCTIVE = "destructive"

_ESCAPING_FLAGS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace"})


class GitBoundaryError(ValueError):
    def __init__(self, message: str, code: str = "GIT_BOUNDARY"):
        super().__init__(message)
        self.code = code


def allowed_git_roots() -> list[Path]:
    roots: list[Path] = [get_repo_root().resolve()]
    proj = resolve_project_cwd()
    if proj:
        try:
            roots.append(Path(proj).resolve())
        except Exception:
            pass
    extra = os.environ.get("IMMORTILITY_GIT_ALLOWED_ROOTS", "")
    sep = os.pathsep
    for part in extra.split(sep):
        part = part.strip().strip('"')
        if not part:
            continue
        path = Path(part)
        if path.is_dir():
            roots.append(path.resolve())
    # Unique, keep order
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _inside_allowed(path: Path, roots: list[Path] | None = None) -> bool:
    resolved = path.resolve()
    for root in roots or allowed_git_roots():
        if resolved == root or _is_under(resolved, root):
            return True
    return False


def resolve_git_repo(cwd: str | None = None) -> Path:
    """Resolve a git worktree and enforce allowed-root boundaries."""
    if cwd:
        candidate = Path(os.path.expanduser(str(cwd))).resolve()
    else:
        fallback = resolve_project_cwd() or str(get_repo_root())
        candidate = Path(fallback).resolve()

    if not candidate.is_dir():
        raise GitBoundaryError(f"cwd is not a directory: {candidate}", "INVALID_CWD")
    if not _inside_allowed(candidate):
        raise GitBoundaryError(
            f"cwd is outside allowed git roots: {candidate}. "
            "Set IMMORTILITY_GIT_ALLOWED_ROOTS or use the active project / Immortility repo.",
            "GIT_BOUNDARY",
        )

    raw = CommandTool.run_argv(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(candidate),
        timeout=15.0,
        max_output=8_000,
    )
    if raw.get("status") == "error" or raw.get("returncode") not in (0, "0"):
        err = (raw.get("stderr") or raw.get("message") or "not a git repository").strip()
        raise GitBoundaryError(f"Not a git repository: {candidate}. {err}", "NOT_A_REPO")
    toplevel = Path((raw.get("stdout") or "").strip()).resolve()
    if not toplevel.is_dir():
        raise GitBoundaryError(f"git toplevel is not a directory: {toplevel}", "NOT_A_REPO")
    if not _inside_allowed(toplevel):
        raise GitBoundaryError(
            f"repository root is outside allowed git roots: {toplevel}",
            "GIT_BOUNDARY",
        )
    return toplevel


def classify_git_argv(argv: Sequence[str]) -> str:
    """Classify a git argv (without the leading git binary)."""
    args = [str(a) for a in argv]
    return classify_argv(["git", *args])


def _boundary_error(exc: GitBoundaryError) -> dict[str, Any]:
    return {
        "status": "error",
        "error_code": exc.code,
        "message": str(exc),
        "stdout": "",
        "stderr": str(exc),
        "returncode": None,
    }


def _reject_escaping(extra: Sequence[str]) -> dict[str, Any] | None:
    for a in extra:
        s = str(a)
        base = s.split("=", 1)[0]
        if s in _ESCAPING_FLAGS or base in _ESCAPING_FLAGS:
            return {
                "status": "error",
                "error_code": "GIT_FLAG_BLOCKED",
                "message": f"Flag {s} is not allowed (would escape the repository boundary).",
            }
    return None


def _destructive_blocked(why: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error_code": "DESTRUCTIVE_CONFIRMATION_REQUIRED",
        "message": (
            f"{why} Destructive git operations never run silently. "
            "The user must confirm, then the call is retried with confirm_destructive=true."
        ),
        "classification": CLASS_DESTRUCTIVE,
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def git_run(
    git_args: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: float = 30.0,
    max_output: int | None = 200_000,
    confirm_destructive: bool = False,
) -> dict[str, Any]:
    """Run ``git <args>`` inside a bounded repo via CommandTool.run_argv."""
    blocked = _reject_escaping(git_args)
    if blocked:
        return blocked
    classification = classify_git_argv(git_args)
    if classification == CLASS_DESTRUCTIVE and not _truthy(confirm_destructive):
        return _destructive_blocked(
            f"Refusing `git {' '.join(str(a) for a in git_args)}`."
        )
    try:
        repo = resolve_git_repo(cwd)
    except GitBoundaryError as exc:
        return _boundary_error(exc)

    raw = CommandTool.run_argv(
        ["git", "-C", str(repo), *[str(a) for a in git_args]],
        cwd=str(repo),
        timeout=timeout,
        max_output=max_output,
    )
    raw["repo"] = str(repo)
    raw["classification"] = classification
    raw["git_args"] = [str(a) for a in git_args]
    return raw


def _ok(raw: dict[str, Any]) -> bool:
    if raw.get("status") == "error":
        return False
    return raw.get("returncode") in (0, "0", None)


def _fail_from(raw: dict[str, Any], code: str = "GIT_FAILED") -> dict[str, Any]:
    if raw.get("error_code") in {
        "GIT_BOUNDARY",
        "NOT_A_REPO",
        "INVALID_CWD",
        "DESTRUCTIVE_CONFIRMATION_REQUIRED",
        "GIT_FLAG_BLOCKED",
        "COMMAND_NOT_FOUND",
        "TIMEOUT",
        "CANCELLED",
    }:
        return raw
    msg = (raw.get("stderr") or raw.get("message") or raw.get("stdout") or "git failed").strip()
    return {
        "status": "error",
        "error_code": raw.get("error_code") or code,
        "message": msg[:2000],
        "returncode": raw.get("returncode"),
        "stdout": raw.get("stdout") or "",
        "stderr": raw.get("stderr") or "",
        "cwd": raw.get("cwd") or raw.get("repo"),
        "repo": raw.get("repo"),
        "classification": raw.get("classification"),
        "timed_out": raw.get("timed_out", False),
        "cancelled": raw.get("cancelled", False),
    }


def _parse_status_porcelain(text: str) -> dict[str, Any]:
    lines = (text or "").splitlines()
    branch = ""
    upstream = ""
    ahead = 0
    behind = 0
    files: list[dict[str, str]] = []
    for line in lines:
        if line.startswith("## "):
            header = line[3:].strip()
            if "..." in header:
                branch, rest = header.split("...", 1)
                upstream = rest.split(" ", 1)[0]
                if "[" in rest:
                    extra = rest[rest.index("[") + 1 : rest.rindex("]")] if "]" in rest else ""
                    for part in extra.split(","):
                        part = part.strip()
                        if part.startswith("ahead "):
                            try:
                                ahead = int(part.split()[1])
                            except (IndexError, ValueError):
                                pass
                        elif part.startswith("behind "):
                            try:
                                behind = int(part.split()[1])
                            except (IndexError, ValueError):
                                pass
            else:
                branch = header.replace("No commits yet on ", "").strip()
            continue
        if len(line) >= 3:
            files.append({"xy": line[:2], "path": line[3:]})
    return {
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "files": files,
        "clean": not files,
    }


class GitTool:
    """First-class git primitives. Read-only ops skip confirmation at the kernel."""

    @staticmethod
    def status(cwd: str | None = None) -> dict[str, Any]:
        raw = git_run(["status", "--porcelain=v1", "-b"], cwd=cwd, timeout=20.0)
        if not _ok(raw):
            return _fail_from(raw)
        parsed = _parse_status_porcelain(raw.get("stdout") or "")
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_READONLY,
            "branch": parsed["branch"],
            "upstream": parsed["upstream"],
            "ahead": parsed["ahead"],
            "behind": parsed["behind"],
            "clean": parsed["clean"],
            "files": parsed["files"],
        }

    @staticmethod
    def diff(
        cwd: str | None = None,
        staged: bool = False,
        path: str | None = None,
        commit: str | None = None,
    ) -> dict[str, Any]:
        args: list[str] = ["diff"]
        if _truthy(staged):
            args.append("--cached")
        if commit:
            args.append(str(commit))
        if path:
            args.extend(["--", str(path)])
        raw = git_run(args, cwd=cwd, timeout=30.0)
        if not _ok(raw):
            return _fail_from(raw)
        patch = raw.get("stdout") or ""
        files = [
            line[6:]
            for line in patch.splitlines()
            if line.startswith("diff --git ")
        ]
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_READONLY,
            "staged": _truthy(staged),
            "files": files,
            "patch": patch,
            "truncated": bool(raw.get("truncated")),
        }

    @staticmethod
    def log(cwd: str | None = None, max_count: int = 20, path: str | None = None) -> dict[str, Any]:
        try:
            n = int(max_count)
        except (TypeError, ValueError):
            n = 20
        n = max(1, min(n, 100))
        args = ["log", f"-n{n}", "--format=%H%x09%an%x09%ae%x09%cI%x09%s"]
        if path:
            args.extend(["--", str(path)])
        raw = git_run(args, cwd=cwd, timeout=20.0)
        if not _ok(raw):
            return _fail_from(raw)
        commits = []
        for line in (raw.get("stdout") or "").splitlines():
            parts = line.split("\t", 4)
            if len(parts) < 5:
                continue
            commits.append(
                {
                    "sha": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3],
                    "subject": parts[4],
                }
            )
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_READONLY,
            "commits": commits,
        }

    @staticmethod
    def show(rev: str = "HEAD", cwd: str | None = None) -> dict[str, Any]:
        rev = (rev or "HEAD").strip() or "HEAD"
        raw = git_run(
            ["show", "--stat", "--format=%H%x09%an%x09%ae%x09%cI%x09%s", rev],
            cwd=cwd,
            timeout=20.0,
        )
        if not _ok(raw):
            return _fail_from(raw)
        text = raw.get("stdout") or ""
        first = text.splitlines()[0] if text.splitlines() else ""
        parts = first.split("\t", 4)
        meta = {}
        if len(parts) >= 5:
            meta = {
                "sha": parts[0],
                "author": parts[1],
                "email": parts[2],
                "date": parts[3],
                "subject": parts[4],
            }
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_READONLY,
            "rev": rev,
            **meta,
            "body": text,
            "truncated": bool(raw.get("truncated")),
        }

    @staticmethod
    def branch(
        cwd: str | None = None,
        all: bool = False,
        delete: str | None = None,
        force: bool = False,
        confirm_destructive: bool = False,
    ) -> dict[str, Any]:
        if delete:
            flag = "-D" if _truthy(force) else "-d"
            raw = git_run(
                ["branch", flag, str(delete)],
                cwd=cwd,
                timeout=20.0,
                confirm_destructive=_truthy(confirm_destructive),
            )
            if not _ok(raw):
                return _fail_from(raw)
            return {
                "status": "success",
                "repo": raw.get("repo"),
                "classification": CLASS_DESTRUCTIVE,
                "deleted": str(delete),
                "force": _truthy(force),
            }
        args = ["branch", "--list", "--format=%(HEAD)%09%(refname:short)%09%(upstream:short)"]
        if _truthy(all):
            args.insert(1, "-a")
        raw = git_run(args, cwd=cwd, timeout=20.0)
        if not _ok(raw):
            return _fail_from(raw)
        branches = []
        current = ""
        for line in (raw.get("stdout") or "").splitlines():
            parts = line.split("\t")
            if not parts:
                continue
            name = parts[1] if len(parts) > 1 else parts[0].lstrip("* ")
            is_head = parts[0].strip() == "*"
            if is_head:
                current = name
            branches.append(
                {
                    "name": name,
                    "current": is_head,
                    "upstream": parts[2] if len(parts) > 2 else "",
                }
            )
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_READONLY,
            "current": current,
            "branches": branches,
        }

    @staticmethod
    def checkout(
        target: str,
        cwd: str | None = None,
        create: bool = False,
    ) -> dict[str, Any]:
        target = (target or "").strip()
        if not target:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": "checkout/switch requires a branch or revision.",
            }
        args = ["switch"]
        if _truthy(create):
            args.append("-c")
        args.append(target)
        raw = git_run(args, cwd=cwd, timeout=30.0)
        if not _ok(raw):
            # Older git: fall back to checkout
            fallback = ["checkout"]
            if _truthy(create):
                fallback.append("-b")
            fallback.append(target)
            raw = git_run(fallback, cwd=cwd, timeout=30.0)
        if not _ok(raw):
            return _fail_from(raw)
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_MUTATING,
            "target": target,
            "created": _truthy(create),
            "stderr": raw.get("stderr") or "",
        }

    @staticmethod
    def switch(
        target: str,
        cwd: str | None = None,
        create: bool = False,
    ) -> dict[str, Any]:
        return GitTool.checkout(target=target, cwd=cwd, create=create)

    @staticmethod
    def add(paths: Any = None, cwd: str | None = None, all: bool = False) -> dict[str, Any]:
        try:
            repo = resolve_git_repo(cwd)
        except GitBoundaryError as exc:
            return _boundary_error(exc)
        args = ["add"]
        if _truthy(all) and not paths:
            args.append("-A")
        else:
            if isinstance(paths, str):
                path_list = [paths]
            else:
                path_list = list(paths or [])
            if not path_list:
                return {
                    "status": "error",
                    "error_code": "INVALID_ARGS",
                    "message": "git_add requires paths or all=true.",
                }
            safe: list[str] = []
            for p in path_list:
                # Allow repo-relative paths; reject escapes.
                candidate = Path(p)
                if candidate.is_absolute():
                    resolved = candidate.resolve()
                    if not _is_under(resolved, repo) and resolved != repo:
                        return {
                            "status": "error",
                            "error_code": "GIT_BOUNDARY",
                            "message": f"path is outside the repository: {resolved}",
                        }
                    try:
                        rel = resolved.relative_to(repo)
                        safe.append(str(rel).replace("\\", "/"))
                    except ValueError:
                        return {
                            "status": "error",
                            "error_code": "GIT_BOUNDARY",
                            "message": f"path is outside the repository: {resolved}",
                        }
                else:
                    if ".." in Path(p).parts:
                        return {
                            "status": "error",
                            "error_code": "GIT_BOUNDARY",
                            "message": f"path must stay inside the repository: {p}",
                        }
                    safe.append(str(p).replace("\\", "/"))
            args.append("--")
            args.extend(safe)
        raw = git_run(args, cwd=str(repo), timeout=30.0)
        if not _ok(raw):
            return _fail_from(raw)
        return {
            "status": "success",
            "repo": str(repo),
            "classification": CLASS_MUTATING,
            "added": args[args.index("--") + 1 :] if "--" in args else ["-A"],
        }

    @staticmethod
    def commit(
        message: str,
        cwd: str | None = None,
        amend: bool = False,
        confirm_destructive: bool = False,
    ) -> dict[str, Any]:
        message = (message or "").strip()
        if not message:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": "git_commit requires a message.",
            }
        args = ["commit", "-m", message]
        if _truthy(amend):
            args.append("--amend")
            # Amend rewrites HEAD — treat as destructive.
            raw = git_run(
                args,
                cwd=cwd,
                timeout=30.0,
                confirm_destructive=_truthy(confirm_destructive),
            )
        else:
            raw = git_run(args, cwd=cwd, timeout=30.0)
        if not _ok(raw):
            return _fail_from(raw)
        sha_raw = git_run(["rev-parse", "HEAD"], cwd=cwd, timeout=10.0)
        sha = (sha_raw.get("stdout") or "").strip() if _ok(sha_raw) else ""
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_DESTRUCTIVE if _truthy(amend) else CLASS_MUTATING,
            "message": message,
            "amend": _truthy(amend),
            "sha": sha,
            "stdout": raw.get("stdout") or "",
        }

    @staticmethod
    def stash(
        action: str = "push",
        cwd: str | None = None,
        message: str | None = None,
        confirm_destructive: bool = False,
    ) -> dict[str, Any]:
        action = (action or "push").strip().lower()
        if action in {"list", "show"}:
            raw = git_run(["stash", action], cwd=cwd, timeout=20.0)
            if not _ok(raw):
                return _fail_from(raw)
            entries = [ln for ln in (raw.get("stdout") or "").splitlines() if ln.strip()]
            return {
                "status": "success",
                "repo": raw.get("repo"),
                "classification": CLASS_READONLY,
                "action": action,
                "entries": entries,
            }
        if action in {"drop", "clear"}:
            raw = git_run(
                ["stash", action],
                cwd=cwd,
                timeout=20.0,
                confirm_destructive=_truthy(confirm_destructive),
            )
        elif action in {"push", "pop", "apply"}:
            args = ["stash", action]
            if action == "push" and message:
                args.extend(["-m", str(message)])
            raw = git_run(args, cwd=cwd, timeout=30.0)
        else:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": f"Unsupported stash action: {action}",
            }
        if not _ok(raw):
            return _fail_from(raw)
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_DESTRUCTIVE if action in {"drop", "clear"} else CLASS_MUTATING,
            "action": action,
            "stdout": raw.get("stdout") or "",
            "stderr": raw.get("stderr") or "",
        }

    @staticmethod
    def fetch(cwd: str | None = None, remote: str = "origin") -> dict[str, Any]:
        args = ["fetch"]
        if remote:
            args.append(str(remote))
        raw = git_run(args, cwd=cwd, timeout=60.0)
        if not _ok(raw):
            return _fail_from(raw)
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_MUTATING,
            "remote": remote,
            "stderr": raw.get("stderr") or "",
        }

    @staticmethod
    def push(
        cwd: str | None = None,
        remote: str = "origin",
        branch: str | None = None,
        force: bool = False,
        confirm_destructive: bool = False,
    ) -> dict[str, Any]:
        args = ["push"]
        if _truthy(force):
            args.append("--force")
        if remote:
            args.append(str(remote))
        if branch:
            args.append(str(branch))
        raw = git_run(
            args,
            cwd=cwd,
            timeout=60.0,
            confirm_destructive=_truthy(force) and _truthy(confirm_destructive),
        )
        # Non-force push is mutating (still confirmed by pending_action), not argv-destructive.
        if _truthy(force) and not _truthy(confirm_destructive):
            # git_run already blocked; keep that result
            if not _ok(raw):
                return _fail_from(raw)
        if not _ok(raw):
            return _fail_from(raw)
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_DESTRUCTIVE if _truthy(force) else CLASS_MUTATING,
            "remote": remote,
            "branch": branch,
            "force": _truthy(force),
            "stderr": raw.get("stderr") or "",
            "stdout": raw.get("stdout") or "",
        }

    @staticmethod
    def reset(
        mode: str = "mixed",
        target: str = "HEAD",
        cwd: str | None = None,
        confirm_destructive: bool = False,
    ) -> dict[str, Any]:
        mode = (mode or "mixed").strip().lower().lstrip("-")
        if mode not in {"soft", "mixed", "hard", "keep", "merge"}:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": f"Unsupported reset mode: {mode}",
            }
        args = ["reset", f"--{mode}", str(target or "HEAD")]
        raw = git_run(
            args,
            cwd=cwd,
            timeout=30.0,
            confirm_destructive=_truthy(confirm_destructive) if mode in {"hard", "keep"} else False,
        )
        if not _ok(raw):
            return _fail_from(raw)
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_DESTRUCTIVE if mode in {"hard", "keep"} else CLASS_MUTATING,
            "mode": mode,
            "target": target,
            "stdout": raw.get("stdout") or "",
            "stderr": raw.get("stderr") or "",
        }

    @staticmethod
    def remote(cwd: str | None = None) -> dict[str, Any]:
        raw = git_run(["remote", "-v"], cwd=cwd, timeout=15.0)
        if not _ok(raw):
            return _fail_from(raw)
        remotes = []
        for line in (raw.get("stdout") or "").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                remotes.append({"name": parts[0], "url": parts[1], "role": parts[2] if len(parts) > 2 else ""})
        return {
            "status": "success",
            "repo": raw.get("repo"),
            "classification": CLASS_READONLY,
            "remotes": remotes,
        }


_REPO_CHANGE_PATTERNS = (
    r"\bmade\s+changes\b",
    r"\bwhat(?:'s|s)?\s+changed\b",
    r"\bwhat\s+did\s+(?:i|you|we)\s+change\b",
    r"\bfind\s+(?:out\s+)?(?:those\s+|the\s+)?changes\b",
    r"\b(show|list|detect|report)\b.{0,40}\b(changes|diff|modifications)\b",
    r"\bchanges\s+in\s+(?:your\s+)?(?:code|codebase|code\s*base|repo|repository)\b",
    r"\bgit\s+(?:status|diff|log)\b",
    r"\buncommitted\b",
    r"\bworking\s+tree\b",
    r"\bwhat(?:'s|s)?\s+(?:different|modified)\b",
)


def wants_repo_changes(message: str) -> bool:
    """True when the user wants local git/working-tree changes, not a folder map."""
    import re

    low = (message or "").lower().strip()
    if not low:
        return False
    # Implement-the-plan confirmations are not a diff request.
    if re.search(r"\bmake\s+(?:the|these)\s+changes\b", low):
        return False
    return any(re.search(pat, low) for pat in _REPO_CHANGE_PATTERNS)


def collect_repo_change_evidence(cwd: str | None = None) -> dict[str, Any]:
    """Read-only git primitives only — status, unstaged/staged diff, recent log."""
    from core.repo_paths import get_repo_root

    root = cwd or str(get_repo_root())
    status = GitTool.status(cwd=root)
    diff = GitTool.diff(cwd=root)
    staged = GitTool.diff(cwd=root, staged=True)
    log = GitTool.log(cwd=root, max_count=8)
    return {
        "cwd": root,
        "status": status,
        "diff": diff,
        "staged": staged,
        "log": log,
    }


def format_repo_change_evidence(bundle: dict[str, Any], *, max_patch: int = 12_000) -> str:
    """Plain evidence block. The model may only cite files listed here."""
    lines: list[str] = []
    status = bundle.get("status") or {}
    if status.get("status") != "success":
        lines.append(
            f"git_status failed: {status.get('error_code')} {status.get('message')}"
        )
        return "\n".join(lines)
    lines.append(f"repo: {status.get('repo')}")
    lines.append(f"branch: {status.get('branch') or '(unknown)'}")
    if status.get("upstream"):
        lines.append(f"upstream: {status['upstream']} ahead={status.get('ahead')} behind={status.get('behind')}")
    files = status.get("files") or []
    lines.append(f"working_tree_clean: {bool(status.get('clean'))}")
    if files:
        lines.append("changed_paths:")
        for row in files:
            lines.append(f"  - {row.get('xy', '  ')} {row.get('path')}")
    else:
        lines.append("changed_paths: (none)")

    def _patch(label: str, diff: dict[str, Any]) -> None:
        if diff.get("status") != "success":
            lines.append(f"{label}: error {diff.get('error_code')} {diff.get('message')}")
            return
        patch = (diff.get("patch") or "").strip()
        names = diff.get("files") or []
        lines.append(f"{label}_files: {names or '(none)'}")
        if patch:
            if len(patch) > max_patch:
                patch = patch[:max_patch] + f"\n...[truncated {len(patch) - max_patch} chars]"
            lines.append(f"{label}_patch:")
            lines.append(patch)
        else:
            lines.append(f"{label}_patch: (empty)")

    _patch("unstaged", bundle.get("diff") or {})
    _patch("staged", bundle.get("staged") or {})

    log = bundle.get("log") or {}
    if log.get("status") == "success":
        lines.append("recent_commits:")
        for c in log.get("commits") or []:
            lines.append(f"  - {c.get('sha', '')[:10]} {c.get('subject')}")
    return "\n".join(lines)


def answer_repo_changes(message: str, cwd: str | None = None) -> str:
    """Compose git_status/git_diff/git_log, then summarize ONLY that evidence."""
    from datetime import date

    bundle = collect_repo_change_evidence(cwd)
    evidence = format_repo_change_evidence(bundle)
    today = date.today().isoformat()
    system = (
        "You are Immortility reporting REAL git working-tree changes. "
        f"Today's date is {today}. "
        "Use ONLY the git evidence below. Never invent files, scripts, docs, "
        "line counts, or dates. If a path is not listed under changed_paths / "
        "unstaged_files / staged_files, it was not changed. "
        "If working_tree_clean is true, say the tree is clean (uncommitted). "
        "Recent commits are history, not uncommitted work — label them separately. "
        "Address Reyansh briefly. Do not write a fake audit memo."
    )
    prompt = (
        f"User asked: {message}\n\n"
        f"## git evidence\n\n{evidence[:14000]}\n\n"
        "List the actual changed files and a short honest summary."
    )
    try:
        from core.config import get_config
        from core.llm import fast_chat

        tokens = get_config().chat_max_tokens
        text = fast_chat(prompt, history=[], system=system, max_output_tokens=tokens).strip()
        if text:
            return text
    except Exception:
        pass
    return f"Git working-tree report (tools only; model summarize unavailable):\n\n{evidence}"
