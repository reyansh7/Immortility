"""Docker primitives — Tool Kernel wrappers over the single command engine.

Read-only inspect first. Destructive ops require explicit confirmation.
Never spawn subprocesses here.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from tools.command_tool import CLASS_DESTRUCTIVE, CommandTool, classify_argv

_DESTRUCTIVE_SUBS = frozenset({"rm", "rmi", "prune", "kill", "system"})


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def docker_run(
    docker_args: Sequence[str],
    *,
    timeout: float = 30.0,
    max_output: int | None = 200_000,
    confirm_destructive: bool = False,
) -> dict[str, Any]:
    args = [str(a) for a in docker_args]
    classification = classify_argv(["docker", *args])
    if classification == CLASS_DESTRUCTIVE and not _truthy(confirm_destructive):
        return {
            "status": "error",
            "error_code": "DESTRUCTIVE_CONFIRMATION_REQUIRED",
            "message": (
                f"Refusing `docker {' '.join(args)}`. Destructive Docker operations "
                "never run silently. Confirm, then retry with confirm_destructive=true."
            ),
            "classification": CLASS_DESTRUCTIVE,
        }
    raw = CommandTool.run_argv(
        ["docker", *args],
        timeout=timeout,
        max_output=max_output,
    )
    raw["classification"] = classification
    raw["docker_args"] = args
    return raw


def _ok(raw: dict[str, Any]) -> bool:
    if raw.get("status") == "error":
        return False
    return raw.get("returncode") in (0, "0", None)


def _fail(raw: dict[str, Any], code: str = "DOCKER_FAILED") -> dict[str, Any]:
    if raw.get("error_code") in {
        "DESTRUCTIVE_CONFIRMATION_REQUIRED",
        "COMMAND_NOT_FOUND",
        "TIMEOUT",
        "CANCELLED",
    }:
        return raw
    msg = (raw.get("stderr") or raw.get("message") or raw.get("stdout") or "docker failed").strip()
    if raw.get("error_code") == "COMMAND_NOT_FOUND" or "The system cannot find" in msg:
        msg = "Docker CLI not found on PATH. Install Docker or start Docker Desktop."
    return {
        "status": "error",
        "error_code": raw.get("error_code") or code,
        "message": msg[:2000],
        "returncode": raw.get("returncode"),
        "stdout": raw.get("stdout") or "",
        "stderr": raw.get("stderr") or "",
        "classification": raw.get("classification"),
        "timed_out": raw.get("timed_out", False),
        "cancelled": raw.get("cancelled", False),
    }


def _parse_json_lines(text: str) -> list[Any]:
    rows: list[Any] = []
    blob = (text or "").strip()
    if not blob:
        return rows
    try:
        parsed = json.loads(blob)
        if isinstance(parsed, list):
            return parsed
        return [parsed]
    except json.JSONDecodeError:
        pass
    for line in blob.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line})
    return rows


class DockerTool:
    @staticmethod
    def ps(all: bool = False) -> dict[str, Any]:
        args = ["ps", "--format", "{{json .}}"]
        if _truthy(all):
            args.insert(1, "-a")
        raw = docker_run(args, timeout=20.0)
        if not _ok(raw):
            return _fail(raw)
        containers = _parse_json_lines(raw.get("stdout") or "")
        return {
            "status": "success",
            "classification": "readonly",
            "containers": containers,
            "count": len(containers),
        }

    @staticmethod
    def images() -> dict[str, Any]:
        raw = docker_run(["images", "--format", "{{json .}}"], timeout=20.0)
        if not _ok(raw):
            return _fail(raw)
        images = _parse_json_lines(raw.get("stdout") or "")
        return {
            "status": "success",
            "classification": "readonly",
            "images": images,
            "count": len(images),
        }

    @staticmethod
    def inspect(target: str) -> dict[str, Any]:
        target = (target or "").strip()
        if not target:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": "docker_inspect requires a container or image id/name.",
            }
        raw = docker_run(["inspect", target], timeout=20.0)
        if not _ok(raw):
            return _fail(raw)
        try:
            data = json.loads(raw.get("stdout") or "[]")
        except json.JSONDecodeError:
            data = raw.get("stdout") or ""
        return {
            "status": "success",
            "classification": "readonly",
            "target": target,
            "inspect": data,
        }

    @staticmethod
    def logs(container: str, tail: int = 100) -> dict[str, Any]:
        container = (container or "").strip()
        if not container:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": "docker_logs requires a container id/name.",
            }
        try:
            n = max(1, min(int(tail), 5000))
        except (TypeError, ValueError):
            n = 100
        raw = docker_run(["logs", "--tail", str(n), container], timeout=20.0)
        if not _ok(raw):
            return _fail(raw)
        return {
            "status": "success",
            "classification": "readonly",
            "container": container,
            "tail": n,
            "logs": raw.get("stdout") or "",
            "stderr": raw.get("stderr") or "",
            "truncated": bool(raw.get("truncated")),
        }

    @staticmethod
    def info() -> dict[str, Any]:
        raw = docker_run(["info", "--format", "{{json .}}"], timeout=20.0)
        if not _ok(raw):
            # Older docker may not support --format json
            raw = docker_run(["info"], timeout=20.0)
            if not _ok(raw):
                return _fail(raw)
            return {
                "status": "success",
                "classification": "readonly",
                "info_text": raw.get("stdout") or "",
            }
        try:
            info = json.loads(raw.get("stdout") or "{}")
        except json.JSONDecodeError:
            info = {"raw": raw.get("stdout") or ""}
        return {"status": "success", "classification": "readonly", "info": info}

    @staticmethod
    def version() -> dict[str, Any]:
        raw = docker_run(["version", "--format", "{{json .}}"], timeout=15.0)
        if not _ok(raw):
            raw = docker_run(["version"], timeout=15.0)
            if not _ok(raw):
                return _fail(raw)
            return {
                "status": "success",
                "classification": "readonly",
                "version_text": raw.get("stdout") or "",
            }
        try:
            version = json.loads(raw.get("stdout") or "{}")
        except json.JSONDecodeError:
            version = {"raw": raw.get("stdout") or ""}
        return {"status": "success", "classification": "readonly", "version": version}

    @staticmethod
    def stop(container: str, confirm_destructive: bool = False) -> dict[str, Any]:
        container = (container or "").strip()
        if not container:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": "docker_stop requires a container id/name.",
            }
        # stop is mutating; still go through command engine. pending_action confirms.
        raw = docker_run(["stop", container], timeout=40.0)
        if not _ok(raw):
            return _fail(raw)
        return {
            "status": "success",
            "classification": "mutating",
            "container": container,
            "stdout": raw.get("stdout") or "",
        }

    @staticmethod
    def rm(
        target: str,
        force: bool = False,
        confirm_destructive: bool = False,
    ) -> dict[str, Any]:
        target = (target or "").strip()
        if not target:
            return {
                "status": "error",
                "error_code": "INVALID_ARGS",
                "message": "docker_rm requires a container id/name.",
            }
        args = ["rm"]
        if _truthy(force):
            args.append("-f")
        args.append(target)
        raw = docker_run(
            args,
            timeout=30.0,
            confirm_destructive=_truthy(confirm_destructive),
        )
        if not _ok(raw):
            return _fail(raw)
        return {
            "status": "success",
            "classification": "destructive",
            "target": target,
            "force": _truthy(force),
            "stdout": raw.get("stdout") or "",
        }
