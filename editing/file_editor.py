import logging
from pathlib import Path

from editing.diff_logger import DiffLogger
from editing.patch_validator import PatchValidator

logger = logging.getLogger(__name__)

_backups: dict[str, str] = {}


class FileEditor:
    """
    Applies patches, verifies targets exist, maintains formatting,
    logs unified diffs, and supports rollback on failure.
    """

    _diff_logger = DiffLogger()

    @staticmethod
    def has_backup(file_path: str) -> bool:
        return file_path in _backups

    @staticmethod
    def backup(file_path: str) -> None:
        path = Path(file_path)
        if path.exists():
            _backups[file_path] = path.read_text(encoding="utf-8")

    @staticmethod
    def rollback(file_path: str) -> dict:
        if file_path not in _backups:
            return {"status": "error", "message": f"No backup for {file_path}"}
        Path(file_path).write_text(_backups[file_path], encoding="utf-8")
        logger.info("Rolled back %s", file_path)
        return {"status": "success", "message": f"Rolled back {file_path}"}

    @staticmethod
    def _write_with_diff(file_path: str, old_content: str, new_content: str, operation: str) -> None:
        FileEditor._diff_logger.log_edit(file_path, old_content, new_content, operation)
        Path(file_path).write_text(new_content, encoding="utf-8")

    @staticmethod
    def apply_search_replace(file_path: str, target_text: str, replacement_text: str) -> dict:
        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"File {file_path} not found"}

        content = path.read_text(encoding="utf-8")
        valid, msg = PatchValidator.validate_search_replace(content, target_text)
        if not valid:
            return {"status": "error", "message": msg}

        new_content = content.replace(target_text, replacement_text, 1)
        valid, msg = PatchValidator.validate_syntax(new_content, file_path)
        if not valid:
            return {"status": "error", "message": f"Patch would introduce a syntax error: {msg}"}

        FileEditor.backup(file_path)
        FileEditor._write_with_diff(file_path, content, new_content, "search_replace")
        logger.info("Edited %s via search_replace", file_path)
        return {"status": "success", "message": f"Edited {file_path}"}

    @staticmethod
    def replace_lines(file_path: str, start_line: int, end_line: int, replacement: str) -> dict:
        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"File {file_path} not found"}

        try:
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")

            if start_line < 1 or end_line < start_line or start_line > len(lines):
                return {
                    "status": "error",
                    "message": f"Invalid line range {start_line}-{end_line} for {len(lines)} lines",
                }
            if end_line > len(lines):
                return {
                    "status": "error",
                    "message": f"end_line {end_line} exceeds file length ({len(lines)} lines)",
                }

            new_lines = lines[: start_line - 1] + replacement.split("\n") + lines[end_line:]
            new_content = "\n".join(new_lines)

            valid, msg = PatchValidator.validate_syntax(new_content, file_path)
            if not valid:
                return {"status": "error", "message": f"Patch would introduce a syntax error: {msg}"}

            FileEditor.backup(file_path)
            FileEditor._write_with_diff(file_path, content, new_content, "replace_lines")
            logger.info("Replaced lines %d-%d in %s", start_line, end_line, file_path)
            return {"status": "success", "message": f"Replaced lines {start_line}-{end_line} in {file_path}"}
        except Exception as e:
            logger.error("Failed to replace lines in %s: %s", file_path, e)
            return {"status": "error", "message": str(e)}

    @staticmethod
    def append_to_file(file_path: str, content_to_append: str) -> dict:
        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"File {file_path} not found"}

        content = path.read_text(encoding="utf-8")
        if not content.endswith("\n"):
            content += "\n"
        new_content = content + content_to_append

        valid, msg = PatchValidator.validate_syntax(new_content, file_path)
        if not valid:
            return {"status": "error", "message": f"Append would introduce a syntax error: {msg}"}

        FileEditor.backup(file_path)
        FileEditor._write_with_diff(file_path, content, new_content, "append")
        return {"status": "success", "message": f"Appended to {file_path}"}

    @staticmethod
    def apply_unified_patch(file_path: str, patch: str) -> dict:
        """Apply a unified diff patch to a file."""
        from editing.unified_diff import apply_unified_diff

        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"File {file_path} not found"}

        content = path.read_text(encoding="utf-8")
        new_content = apply_unified_diff(content, patch)
        if new_content is None:
            return {"status": "error", "message": "Failed to apply patch — invalid or mismatched diff"}

        valid, msg = PatchValidator.validate_syntax(new_content, file_path)
        if not valid:
            return {"status": "error", "message": f"Patch would introduce a syntax error: {msg}"}

        FileEditor.backup(file_path)
        FileEditor._write_with_diff(file_path, content, new_content, "apply_patch")
        return {"status": "success", "message": f"Applied patch to {file_path}"}

    @staticmethod
    def rename_symbol_in_file(file_path: str, old_name: str, new_name: str) -> dict:
        from editing.ast_editor import ASTEditor

        path = Path(file_path)
        if not path.exists():
            return {"status": "error", "message": f"File {file_path} not found"}

        content = path.read_text(encoding="utf-8")
        editor = ASTEditor()
        new_content = editor.rename_symbol(content, old_name, new_name)
        if new_content is None or new_content == content:
            return {"status": "error", "message": f"Symbol '{old_name}' not found in {file_path}"}

        valid, msg = PatchValidator.validate_syntax(new_content, file_path)
        if not valid:
            return {"status": "error", "message": f"Rename would introduce a syntax error: {msg}"}

        FileEditor.backup(file_path)
        FileEditor._write_with_diff(file_path, content, new_content, "rename_symbol")
        return {"status": "success", "message": f"Renamed {old_name} → {new_name} in {file_path}"}
