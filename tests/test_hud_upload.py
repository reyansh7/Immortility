"""HUD document attach/parse limits and extract_document flattening."""

from __future__ import annotations

from pathlib import Path

from tools.document_tool import extract_document, extracted_text
from tools.hud_upload import (
    MAX_FILE_BYTES,
    compose_attachment_context,
    parse_multipart_files,
    reset_upload_cache,
    save_and_parse,
    safe_filename,
)


def test_safe_filename_strips_paths():
    assert safe_filename("..\\..\\secret.pdf") == "secret.pdf"
    assert safe_filename("/etc/passwd") == "passwd"


def test_csv_extract_includes_text(tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("name,score\nReyansh,10\n", encoding="utf-8")
    rec = extract_document(str(path))
    assert rec["status"] == "success"
    assert "Reyansh" in rec["text"]
    assert "Reyansh" in extracted_text(rec)


def test_md_is_supported(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Hello\nbody", encoding="utf-8")
    rec = extract_document(str(path))
    assert rec["status"] == "success"
    assert "Hello" in rec["text"]


def test_save_and_parse_csv(tmp_path, monkeypatch):
    reset_upload_cache()
    monkeypatch.setattr("tools.hud_upload.uploads_dir", lambda: tmp_path)
    rec = save_and_parse("notes.csv", b"a,b\n1,2\n")
    assert rec["status"] == "success"
    assert rec["name"] == "notes.csv"
    ctx = compose_attachment_context([rec["id"]])
    assert "notes.csv" in ctx
    assert "1,2" in ctx or "a,b" in ctx
    assert "[IMMORTILITY_ATTACHED_DOCS]" in ctx
    assert "Do NOT pip install" in ctx


def test_save_and_parse_rejects_image():
    rec = save_and_parse("photo.png", b"\x89PNG")
    assert rec["status"] == "error"
    assert rec["error_code"] == "VISION_UNAVAILABLE"


def test_legacy_doc_extracts_utf16(tmp_path):
    path = tmp_path / "note.doc"
    path.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + ("Hello Reyansh document body. " * 6).encode("utf-16-le")
    )
    rec = extract_document(str(path))
    assert rec["status"] == "success"
    assert "Hello Reyansh" in rec["text"]


def test_corrupt_pdf_does_not_raise(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"this is not a pdf")
    rec = extract_document(str(path))
    assert rec["status"] == "error"
    assert rec["error_code"] in {"PARSE_FAILED", "PARSER_UNAVAILABLE"}


def test_save_and_parse_rejects_huge():
    rec = save_and_parse("big.txt", b"x" * (MAX_FILE_BYTES + 1))
    assert rec["status"] == "error"
    assert rec["error_code"] == "TOO_LARGE"
    assert "20 MB" in rec["message"]


def test_save_and_parse_accepts_20mb(tmp_path, monkeypatch):
    reset_upload_cache()
    monkeypatch.setattr("tools.hud_upload.uploads_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "tools.hud_upload.extract_document",
        lambda path, max_chars=0: {
            "status": "success",
            "text": "ok",
            "truncated": True,
            "format": "txt",
        },
    )
    rec = save_and_parse("ok.txt", b"x" * (20 * 1024 * 1024))
    assert rec["status"] == "success"


def test_multipart_roundtrip():
    boundary = "----HudTestBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="a.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
        "h,v\n1,2\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    files = parse_multipart_files(
        f"multipart/form-data; boundary={boundary}", body
    )
    assert len(files) == 1
    assert files[0][0] == "a.csv"
    assert b"h,v" in files[0][1]


def test_multipart_binary_pdf_roundtrip():
    payload = b"%PDF-1.4\n" + b"\x00\xff\r\n" * 40 + b"%%EOF"
    boundary = "----HudBin"
    body = (
        b"--" + boundary.encode() + b"\r\n"
        b'Content-Disposition: form-data; name="files"; filename="doc.pdf"\r\n'
        b"Content-Type: application/pdf\r\n\r\n"
        + payload
        + b"\r\n--"
        + boundary.encode()
        + b"--\r\n"
    )
    files = parse_multipart_files(f"multipart/form-data; boundary={boundary}", body)
    assert len(files) == 1
    assert files[0][0] == "doc.pdf"
    assert files[0][1] == payload


def test_multipart_filename_star():
    boundary = "----Star"
    body = (
        b"--" + boundary.encode() + b"\r\n"
        b"Content-Disposition: form-data; name=\"files\"; "
        b"filename*=UTF-8''UML%20Class%20Diagram.pdf\r\n"
        b"Content-Type: application/pdf\r\n\r\n"
        b"%PDF-1.4 test\r\n"
        b"--" + boundary.encode() + b"--\r\n"
    )
    files = parse_multipart_files(f"multipart/form-data; boundary={boundary}", body)
    assert files[0][0] == "UML Class Diagram.pdf"
    assert files[0][1].startswith(b"%PDF")


def test_save_and_parse_keeps_unreadable_pdf(tmp_path, monkeypatch):
    reset_upload_cache()
    monkeypatch.setattr("tools.hud_upload.uploads_dir", lambda: tmp_path)
    rec = save_and_parse("scan.pdf", b"this is not a pdf")
    assert rec["status"] == "success"
    assert rec["id"]
    ctx = compose_attachment_context([rec["id"]])
    assert "scan.pdf" in ctx


def test_attach_from_disk_rejects_outside_home(tmp_path):
    from tools.hud_upload import attach_from_disk

    rec = attach_from_disk(str(tmp_path / "secret.pdf"))
    assert rec["status"] == "error"
    assert rec["error_code"] == "NOT_ALLOWED"


def test_compose_clips_total(monkeypatch, tmp_path):
    reset_upload_cache()
    monkeypatch.setattr("tools.hud_upload.uploads_dir", lambda: tmp_path)
    monkeypatch.setattr("tools.hud_upload.TOTAL_INJECT_CHARS", 400)
    monkeypatch.setattr("tools.hud_upload.PER_FILE_CHARS", 300)
    ids = []
    for i in range(3):
        rec = save_and_parse(f"f{i}.txt", ("WORD%d " % i * 80).encode("utf-8"))
        assert rec["status"] == "success"
        ids.append(rec["id"])
    ctx = compose_attachment_context(ids)
    assert "f0.txt" in ctx
    assert "further attachments omitted" in ctx or "f2.txt" not in ctx


def test_hud_html_has_attach_control():
    html = Path("frontend/immortility_hud.html").read_text(encoding="utf-8")
    assert 'id="attachBtn"' in html
    assert 'id="filePick"' in html
    assert "/hud/upload" in html
    assert "attachments" in html
    assert ".doc" in html
    assert "dataTransfer" in html
    assert "task-rail" in html
    assert "boot-title" in html
    assert "An AI that doesn't just answer" in html
    assert "hud-foot" in html
    assert "renderRichText" in html
    assert 'id="fileDrawer"' not in html
    assert 'id="attachChips"' in html
    assert "filePick.click()" in html
