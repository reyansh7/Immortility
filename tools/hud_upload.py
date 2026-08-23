"""HUD file attachments — parse with extract_document, inject clipped text.

Limits are for this laptop (8 GB VRAM, 8192 context): large files are accepted
and parsed, but only a bounded slice of text is given to the model.
"""

from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Any

from core.repo_paths import get_repo_root
from tools.document_tool import SUPPORTED_EXTENSIONS, extract_document, extracted_text

# Windows "20.0 MB" is often a hair over 20 MiB, and multipart adds headers.
# 25 MiB/file keeps a real 20 MB PDF inside the cap. Text sent to the model
# is still clipped (PER_FILE_CHARS) so 8 GB VRAM / 8192 ctx stays safe.
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 4
MAX_REQUEST_BYTES = 48 * 1024 * 1024
# ~3.5k tokens/file, ~5k tokens total — leaves room for system + chat history.
PER_FILE_CHARS = 14_000
TOTAL_INJECT_CHARS = 20_000

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".tif", ".tiff"}

_lock = threading.Lock()
_cache: dict[str, dict[str, Any]] = {}


def uploads_dir() -> Path:
    path = get_repo_root() / ".immortility" / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str) -> str:
    base = Path(name or "").name
    base = re.sub(r"[^\w.\- ()\[\]]+", "_", base).strip(" .")
    if not base or base in {".", ".."}:
        return "upload.bin"
    return base[:120]


def parse_multipart_files(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """Parse browser FormData file parts. Binary-safe (email.parser corrupts PDFs)."""
    match = re.search(r"boundary=([^;]+)", content_type or "", re.I)
    if not match or not body:
        return []
    boundary = match.group(1).strip().strip('"').encode("ascii", "replace")
    sep = b"--" + boundary
    files: list[tuple[str, bytes]] = []
    for raw in body.split(sep):
        if not raw or raw.startswith(b"--"):
            continue
        if raw.startswith(b"\r\n"):
            raw = raw[2:]
        elif raw.startswith(b"\n"):
            raw = raw[1:]
        header_end = raw.find(b"\r\n\r\n")
        sep_len = 4
        if header_end < 0:
            header_end = raw.find(b"\n\n")
            sep_len = 2
        if header_end < 0:
            continue
        headers = raw[:header_end].decode("utf-8", "replace")
        payload = raw[header_end + sep_len :]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        elif payload.endswith(b"\n"):
            payload = payload[:-1]
        found = re.search(r"filename\*=(?:UTF-8'')?([^;\r\n]+)", headers, re.I)
        if found:
            filename = found.group(1).strip().strip('"')
        else:
            found = re.search(r'filename="([^"]*)"', headers, re.I)
            if not found:
                found = re.search(r"filename=([^;\r\n]+)", headers, re.I)
            if not found:
                continue
            filename = found.group(1).strip().strip('"')
        files.append((filename, payload))
    return files


def save_and_parse(filename: str, data: bytes) -> dict[str, Any]:
    """Persist one upload and extract text. Never executes the file."""
    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return {
            "status": "error",
            "error_code": "VISION_UNAVAILABLE",
            "message": (
                f"{name} is an image. Vision is not configured on this machine "
                "(set OLLAMA_VISION_MODEL to enable). Attach PDF/DOC/DOCX/PPTX/XLSX/CSV/TXT/MD."
            ),
            "name": name,
        }
    if suffix not in SUPPORTED_EXTENSIONS:
        return {
            "status": "error",
            "error_code": "UNSUPPORTED_FORMAT",
            "message": (
                f"Cannot parse {name}. Supported: "
                + ", ".join(sorted(SUPPORTED_EXTENSIONS))
            ),
            "name": name,
        }
    if len(data) > MAX_FILE_BYTES:
        return {
            "status": "error",
            "error_code": "TOO_LARGE",
            "message": (
                f"{name} is over 25 MB. A 20 MB file is allowed — "
                "compress it or split it if it is larger."
            ),
            "name": name,
        }
    if not data:
        return {
            "status": "error",
            "error_code": "EMPTY",
            "message": f"{name} is empty.",
            "name": name,
        }

    upload_id = uuid.uuid4().hex[:12]
    dest = uploads_dir() / f"{upload_id}_{name}"
    dest.write_bytes(data)
    try:
        parsed = extract_document(str(dest), max_chars=PER_FILE_CHARS)
    except Exception as exc:
        return {
            "status": "error",
            "error_code": "PARSE_FAILED",
            "message": f"Could not parse {name}: {exc}",
            "name": name,
        }
    if parsed.get("status") != "success":
        return {
            "status": "error",
            "error_code": parsed.get("error_code") or "PARSE_FAILED",
            "message": parsed.get("message") or f"Failed to parse {name}",
            "name": name,
        }
    text = extracted_text(parsed)[:PER_FILE_CHARS]
    if not text.strip():
        text = (
            f"[No extractable text in {name}. "
            "It may be scanned, image-only, or encrypted.]"
        )
    record = {
        "id": upload_id,
        "name": name,
        "path": str(dest),
        "chars": len(text),
        "truncated": bool(parsed.get("truncated")) or len(extracted_text(parsed)) > PER_FILE_CHARS,
        "format": parsed.get("format"),
        "text": text,
    }
    with _lock:
        _cache[upload_id] = record
        # Keep a small working set so uploads cannot grow unbounded in RAM.
        extra = list(_cache.keys())[:-24]
        for key in extra:
            _cache.pop(key, None)
    return {
        "status": "success",
        "id": upload_id,
        "name": name,
        "chars": record["chars"],
        "truncated": record["truncated"],
        "format": record["format"],
    }


def compose_attachment_context(ids: list[str]) -> str:
    """Build prompt context from previously uploaded files. Clipped for 8k context."""
    chunks: list[str] = []
    used = 0
    with _lock:
        for raw_id in ids:
            rec = _cache.get(str(raw_id).strip())
            if not rec:
                continue
            remain = TOTAL_INJECT_CHARS - used
            if remain <= 200:
                chunks.append("[further attachments omitted — context budget]")
                break
            body = str(rec.get("text") or "")[:remain]
            flag = " (truncated)" if rec.get("truncated") else ""
            block = f"Attached file: {rec.get('name')}{flag}\n{body}"
            chunks.append(block)
            used += len(block)
    if not chunks:
        return ""
    return (
        "The user attached file(s). Answer from this extracted text. "
        "Do not invent pages that are not present.\n\n"
        + "\n\n----\n\n".join(chunks)
    )


def reset_upload_cache() -> None:
    with _lock:
        _cache.clear()
