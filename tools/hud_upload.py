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
from urllib.parse import unquote

from core.repo_paths import get_repo_root
from tools.document_tool import SUPPORTED_EXTENSIONS, extract_document, extracted_text

# Windows "20.0 MB" is often a hair over 20 MiB, and multipart adds headers.
# 25 MiB/file keeps a real 20 MB PDF inside the cap. Text sent to the model
# is still clipped (PER_FILE_CHARS) so 8 GB VRAM / 8192 ctx stays safe.
# Injected into the model prompt so routing can skip Action Engine re-extract.
ATTACHED_DOC_MARKER = "[IMMORTILITY_ATTACHED_DOCS]"

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


def _multipart_filename(headers: str) -> str:
    starred = re.search(r"filename\*=(?:UTF-8''|utf-8'')?([^;\r\n]+)", headers, re.I)
    if starred:
        return unquote(starred.group(1).strip().strip('"'))
    quoted = re.search(r'filename="([^"]*)"', headers, re.I)
    if quoted:
        return unquote(quoted.group(1).strip())
    plain = re.search(r"filename=([^;\r\n]+)", headers, re.I)
    if plain:
        return unquote(plain.group(1).strip().strip('"'))
    return ""


def _guess_upload_name(payload: bytes) -> str:
    head = payload.lstrip()[:8]
    if head.startswith(b"%PDF"):
        return "upload.pdf"
    if head.startswith(b"PK"):
        return "upload.docx"
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "upload.doc"
    return ""


def parse_multipart_files(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """Parse browser FormData file parts. Binary-safe (email.parser corrupts PDFs)."""
    match = re.search(r"boundary=([^;]+)", content_type or "", re.I)
    if not match or not body:
        return []
    boundary = match.group(1).strip().strip('"').encode("ascii", "replace")
    sep = b"--" + boundary
    files: list[tuple[str, bytes]] = []
    for raw in body.split(sep):
        if not raw or raw in {b"--", b"--\r\n", b"--\n"} or raw.startswith(b"--"):
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
        filename = _multipart_filename(headers) or _guess_upload_name(payload)
        if not filename or not payload:
            continue
        files.append((filename, payload))
    return files


def browse_places() -> list[tuple[str, Path]]:
    """Desktop/Documents/Downloads, including OneDrive copies on this laptop."""
    home = Path.home()
    seen: set[Path] = set()
    places: list[tuple[str, Path]] = []
    for label, path in (
        ("Desktop", home / "OneDrive" / "Desktop"),
        ("Desktop", home / "Desktop"),
        ("Documents", home / "OneDrive" / "Documents"),
        ("Documents", home / "Documents"),
        ("Downloads", home / "Downloads"),
        ("Downloads", home / "OneDrive" / "Downloads"),
    ):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_dir() or resolved in seen:
            continue
        seen.add(resolved)
        places.append((label, resolved))
    return places


def list_attachable_files(place: str = "") -> list[dict[str, Any]]:
    """Vertical file list for the HUD picker. Non-recursive, documents only."""
    wanted = (place or "Desktop").strip().lower()
    rows: list[dict[str, Any]] = []
    for label, root in browse_places():
        if wanted and label.lower() != wanted:
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for item in entries:
            if not item.is_file():
                continue
            suffix = item.suffix.lower()
            if suffix not in SUPPORTED_EXTENSIONS:
                continue
            try:
                size = int(item.stat().st_size)
            except OSError:
                continue
            rows.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "place": label,
                    "size": size,
                    "suffix": suffix,
                }
            )
    rows.sort(key=lambda r: r["name"].lower())
    return rows[:200]


def attach_from_disk(path: str) -> dict[str, Any]:
    """Attach a file already on disk (no multipart). Path must be under browse_places()."""
    if not path or not str(path).strip():
        return {
            "status": "error",
            "error_code": "INVALID_ARGS",
            "message": "No file path.",
        }
    target = Path(path).expanduser()
    try:
        resolved = target.resolve()
    except OSError as exc:
        return {
            "status": "error",
            "error_code": "NOT_FOUND",
            "message": f"Cannot open that file ({exc}).",
        }
    allowed = False
    for _label, root in browse_places():
        try:
            resolved.relative_to(root.resolve())
            allowed = True
            break
        except ValueError:
            continue
    if not allowed or not resolved.is_file():
        return {
            "status": "error",
            "error_code": "NOT_ALLOWED",
            "message": "Pick a file from Desktop, Documents, or Downloads.",
        }
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        return {
            "status": "error",
            "error_code": "NOT_FOUND",
            "message": f"Could not read {resolved.name}: {exc}",
        }
    return save_and_parse(resolved.name, data)


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
        parsed = {
            "status": "error",
            "error_code": "PARSE_FAILED",
            "message": f"Could not parse {name}: {exc}",
        }
    extracted = extracted_text(parsed)[:PER_FILE_CHARS] if parsed.get("status") == "success" else ""
    if not extracted.strip():
        extracted = (
            parsed.get("message")
            or f"[Attached {name}. No extractable text — it may be scanned, image-only, or encrypted.]"
        )
    text = extracted[:PER_FILE_CHARS]
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
        f"{ATTACHED_DOC_MARKER}\n"
        "The user attached file(s). The document text is already extracted below. "
        "Do NOT pip install, do NOT call extract_document, do NOT run_command for parsers. "
        "Answer from this text only. Do not invent pages that are not present.\n\n"
        + "\n\n----\n\n".join(chunks)
    )


def reset_upload_cache() -> None:
    with _lock:
        _cache.clear()
