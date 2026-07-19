"""Classify pasted runtime / build errors for deterministic debug routing."""

from __future__ import annotations

import re
from enum import Enum


class ErrorKind(str, Enum):
    RSC_MANIFEST = "rsc_manifest"
    HOME_404 = "home_404"
    BUILD = "build"
    MODULE_NOT_FOUND = "module_not_found"
    LAYOUT_CORRUPTION = "layout_corruption"
    GENERIC = "generic"


def is_error_report(text: str) -> bool:
    lower = text.lower()
    markers = (
        "error",
        "failed",
        "exception",
        "traceback",
        "module not found",
        "cannot find module",
        "npm run build",
        "syntaxerror",
        "typeerror",
        "digest:",
        "react client manifest",
        "get /",
        "404",
        "⨯",
    )
    return sum(1 for m in markers if m in lower) >= 2 or "react client manifest" in lower


def classify_error(text: str) -> ErrorKind:
    lower = text.lower()
    if (
        "react client manifest" in lower
        or "rsc manifest" in lower
        or "manifest file is empty" in lower
        or ("could not find the module" in lower and "#" in text)
    ):
        return ErrorKind.RSC_MANIFEST
    if ("get /" in lower and "404" in lower) or "failed to load resource" in lower and "404" in lower:
        return ErrorKind.HOME_404
    if "module not found" in lower or "can't resolve" in lower:
        return ErrorKind.MODULE_NOT_FOUND
    if "npm run build" in lower or "build error" in lower or "turbopack build failed" in lower:
        return ErrorKind.BUILD
    if "export default function rootlayout" in lower or "jobboard" in lower or "useauth(" in lower:
        return ErrorKind.LAYOUT_CORRUPTION
    if "layout.tsx" in lower and ("usestate" in lower or "duplicate" in lower):
        return ErrorKind.LAYOUT_CORRUPTION
    return ErrorKind.GENERIC


def extract_missing_module(text: str) -> str | None:
    m = re.search(r"Can't resolve ['\"]@?/?([^'\"]+)['\"]", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"Module not found: Can't resolve ['\"]([^'\"]+)['\"]", text, re.I)
    if m:
        return m.group(1)
    return None
