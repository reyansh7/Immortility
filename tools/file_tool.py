import re
from pathlib import Path

from editing.symbol_finder import SymbolFinder


class FileTool:
    """Tool for local file and directory operations."""

    @staticmethod
    def create_file(path: str, content: str = "") -> dict:
        """Create a NEW file only. Refuses to overwrite existing files."""
        try:
            file_path = Path(path)
            if file_path.exists():
                return {
                    "status": "error",
                    "message": (
                        f"File already exists: {path}. "
                        "Use edit_file, replace_lines, or apply_patch to modify existing files."
                    ),
                }
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {"status": "success", "message": f"Created {path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def write_file(path: str, content: str) -> dict:
        """Write to an existing file (creates if missing)."""
        try:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            from editing.file_editor import FileEditor

            if file_path.exists():
                old = file_path.read_text(encoding="utf-8")
                FileEditor.backup(path)
                FileEditor._write_with_diff(path, old, content, "write_file")
            else:
                file_path.write_text(content, encoding="utf-8")
            return {"status": "success", "message": f"Wrote {path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def read_file(path: str) -> dict:
        try:
            p = Path(path)
            if p.is_dir():
                return {"status": "error", "message": f"{path} is a directory. Please use list_directory instead."}
            content = p.read_text(encoding="utf-8")
            lines = content.split("\n")
            numbered = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
            return {
                "status": "success",
                "content": content,
                "numbered": numbered,
                "line_count": len(lines),
                "raw": content,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def list_directory(path: str) -> dict:
        try:
            entries = [x.name for x in Path(path).iterdir()]
            return {"status": "success", "files": entries}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def delete_file(path: str) -> dict:
        try:
            file_path = Path(path)
            if file_path.is_dir():
                return {"status": "error", "message": f"{path} is a directory, not a file"}
            file_path.unlink(missing_ok=True)
            return {"status": "success", "message": f"Deleted {path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def replace_lines(path: str, start_line: int, end_line: int, replacement: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.replace_lines(path, start_line, end_line, replacement)

    @staticmethod
    def edit_file(path: str, target_text: str, replacement_text: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(path, target_text, replacement_text)

    @staticmethod
    def append_file(path: str, content: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.append_to_file(path, content)

    @staticmethod
    def insert_before(path: str, target_text: str, content: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(path, target_text, content + "\n" + target_text)

    @staticmethod
    def insert_after(path: str, target_text: str, content: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(path, target_text, target_text + "\n" + content)

    @staticmethod
    def replace_regex(path: str, pattern: str, replacement: str) -> dict:
        try:
            file_path = Path(path)
            if not file_path.exists():
                return {"status": "error", "message": f"File not found: {path}"}
            old = file_path.read_text(encoding="utf-8")
            new_content = re.sub(pattern, replacement, old)
            from editing.patch_validator import PatchValidator
            valid, msg = PatchValidator.validate_syntax(new_content, path)
            if not valid:
                return {"status": "error", "message": f"Regex replacement would break syntax: {msg}"}
            from editing.file_editor import FileEditor
            FileEditor.backup(path)
            FileEditor._write_with_diff(path, old, new_content, "replace_regex")
            return {"status": "success", "message": f"Applied regex replacement in {path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def delete_block(path: str, target_text: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(path, target_text, "")

    @staticmethod
    def rename_symbol(path: str, old_name: str, new_name: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.rename_symbol_in_file(path, old_name, new_name)

    @staticmethod
    def find_symbol(project: str, symbol: str) -> dict:
        root = Path(project)
        if not root.is_dir():
            return {"status": "error", "message": f"Project not found: {project}"}
        finder = SymbolFinder(root)
        results = finder.find_symbol(symbol)
        return {"status": "success", "symbol": symbol, "definitions": results, "count": len(results)}

    @staticmethod
    def query_code_graph(project: str, query: str) -> dict:
        from knowledge.graph_engine import GraphEngine
        try:
            ge = GraphEngine()
            graph_path = Path(project) / "graphify-out" / "graph.json"
            ge.load_graph(graph_path)
            if not ge.is_loaded():
                return {"status": "error", "message": f"Graph not generated for {project} yet. Open it first."}
            res = ge.query_relationships(query)
            return {"status": "success", "result": res}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def find_references(project: str, symbol: str) -> dict:
        root = Path(project)
        if not root.is_dir():
            return {"status": "error", "message": f"Project not found: {project}"}
        finder = SymbolFinder(root)
        results = finder.find_references(symbol)
        return {"status": "success", "symbol": symbol, "references": results, "count": len(results)}

    @staticmethod
    def generate_diff(old_text: str, new_text: str) -> dict:
        from editing.patch_generator import PatchGenerator
        diff = PatchGenerator.generate_unified_diff(old_text, new_text)
        return {"status": "success", "diff": diff}

    @staticmethod
    def apply_patch(path: str, patch: str) -> dict:
        from editing.file_editor import FileEditor
        return FileEditor.apply_unified_patch(path, patch)

    @staticmethod
    def validate_patch(path: str) -> dict:
        from editing.patch_validator import PatchValidator
        result = PatchValidator.validate_patch(path)
        status = "success" if result["valid"] else "error"
        return {"status": status, **result}
