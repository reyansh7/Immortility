import fnmatch
import os
import re
from pathlib import Path

from editing.symbol_finder import SymbolFinder


def _guard(path: str, *, write: bool = False) -> tuple[Path | None, dict | None]:
    from core.fs_sandbox import check_path

    ok, resolved, err = check_path(path, write=write)
    if not ok or resolved is None:
        return None, {"status": "error", "message": err}
    return resolved, None


class FileTool:
    """Tool for local file and directory operations."""

    @staticmethod
    def create_file(path: str, content: str = "") -> dict:
        """Create a NEW file only. Refuses to overwrite existing files."""
        file_path, err = _guard(path, write=True)
        if err:
            return err
        try:
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
        file_path, err = _guard(path, write=True)
        if err:
            return err
        try:
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
    def read_file_lines(path: str, start_line: int = 1, end_line: int = 100) -> dict:
        """Read a specific range of line numbers from a sandboxed file."""
        p, err = _guard(path, write=False)
        if err:
            return err
        try:
            if not p.is_file():
                return {"status": "error", "message": f"Not a file or file not found: {path}"}
            start = max(1, int(start_line))
            end = max(start, int(end_line))
            lines = []
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for idx, line in enumerate(f, start=1):
                    if start <= idx <= end:
                        lines.append(f"{idx}: {line.rstrip('\r\n')}")
                    elif idx > end:
                        break
            return {
                "status": "success",
                "path": str(p),
                "start_line": start,
                "end_line": end,
                "total_returned": len(lines),
                "numbered": "\n".join(lines),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def search_file_content(
        path: str,
        query: str,
        recursive: bool = True,
        max_results: int = 50,
        is_regex: bool = False,
        case_sensitive: bool = False,
    ) -> dict:
        """Search for text or regex pattern inside the filesystem sandbox."""
        target, err = _guard(path, write=False)
        if err:
            return err
        try:
            if not target.exists():
                return {"status": "error", "message": f"Path not found: {path}"}

            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(query if is_regex else re.escape(query), flags)
            except re.error as e:
                return {"status": "error", "message": f"Invalid regex pattern: {e}"}

            matches = []
            files_to_check = []

            if target.is_file():
                files_to_check.append(target)
            elif recursive:
                skip_dirs = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".pytest_cache"}
                for root, dirs, files in os.walk(target):
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for f in files:
                        files_to_check.append(Path(root) / f)
            else:
                for entry in target.iterdir():
                    if entry.is_file():
                        files_to_check.append(entry)

            total_scanned = 0
            for fpath in files_to_check:
                if len(matches) >= max_results:
                    break
                try:
                    if fpath.stat().st_size > 10 * 1024 * 1024:
                        continue
                    total_scanned += 1
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for line_num, line in enumerate(f, start=1):
                            if pattern.search(line):
                                matches.append({
                                    "file": str(fpath),
                                    "line_number": line_num,
                                    "line_content": line.rstrip("\r\n"),
                                })
                                if len(matches) >= max_results:
                                    break
                except (OSError, UnicodeDecodeError):
                    continue

            return {
                "status": "success",
                "query": query,
                "matches": matches,
                "count": len(matches),
                "scanned_files": total_scanned,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def find_files(
        root_path: str = "",
        pattern: str = "*",
        max_results: int = 50,
    ) -> dict:
        """Find files and directories matching a glob pattern inside the sandbox."""
        try:
            from core.fs_sandbox import default_scan_root

            if not root_path:
                root, err = _guard(str(default_scan_root()), write=False)
            else:
                root, err = _guard(root_path, write=False)
            if err:
                return err
            if not root.exists():
                return {"status": "error", "message": f"Path not found: {root_path}"}

            results = []
            skip_dirs = {".git", "__pycache__", "node_modules", ".pytest_cache"}
            from core.fs_sandbox import DENIED_DIR_NAMES

            for cur_root, dirs, files in os.walk(root):
                dirs[:] = [
                    d for d in dirs
                    if d not in skip_dirs and d.lower() not in DENIED_DIR_NAMES
                ]
                for name in dirs + files:
                    if fnmatch.fnmatch(name.lower(), pattern.lower()):
                        full_path = Path(cur_root) / name
                        try:
                            is_dir = full_path.is_dir()
                            size = 0 if is_dir else full_path.stat().st_size
                            results.append({
                                "path": str(full_path),
                                "name": name,
                                "is_directory": is_dir,
                                "size_bytes": size,
                            })
                            if len(results) >= max_results:
                                break
                        except OSError:
                            continue
                if len(results) >= max_results:
                    break

            return {
                "status": "success",
                "pattern": pattern,
                "root": str(root),
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def read_file(path: str) -> dict:
        p, err = _guard(path, write=False)
        if err:
            return err
        try:
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
        folder, err = _guard(path, write=False)
        if err:
            return err
        try:
            entries = [x.name for x in folder.iterdir()]
            return {"status": "success", "files": entries}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def delete_file(path: str) -> dict:
        file_path, err = _guard(path, write=True)
        if err:
            return err
        try:
            if file_path.is_dir():
                return {"status": "error", "message": f"{path} is a directory, not a file"}
            file_path.unlink(missing_ok=True)
            return {"status": "success", "message": f"Deleted {path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def replace_lines(path: str, start_line: int, end_line: int, replacement: str) -> dict:
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.replace_lines(str(guarded), start_line, end_line, replacement)

    @staticmethod
    def edit_file(path: str, target_text: str, replacement_text: str) -> dict:
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(str(guarded), target_text, replacement_text)

    @staticmethod
    def append_file(path: str, content: str) -> dict:
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.append_to_file(str(guarded), content)

    @staticmethod
    def insert_before(path: str, target_text: str, content: str) -> dict:
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(str(guarded), target_text, content + "\n" + target_text)

    @staticmethod
    def insert_after(path: str, target_text: str, content: str) -> dict:
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(str(guarded), target_text, target_text + "\n" + content)

    @staticmethod
    def replace_regex(path: str, pattern: str, replacement: str) -> dict:
        file_path, err = _guard(path, write=True)
        if err:
            return err
        try:
            if not file_path.exists():
                return {"status": "error", "message": f"File not found: {path}"}
            old = file_path.read_text(encoding="utf-8")
            new_content = re.sub(pattern, replacement, old)
            from editing.patch_validator import PatchValidator
            valid, msg = PatchValidator.validate_syntax(new_content, str(file_path))
            if not valid:
                return {"status": "error", "message": f"Regex replacement would break syntax: {msg}"}
            from editing.file_editor import FileEditor
            FileEditor.backup(str(file_path))
            FileEditor._write_with_diff(str(file_path), old, new_content, "replace_regex")
            return {"status": "success", "message": f"Applied regex replacement in {path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def delete_block(path: str, target_text: str) -> dict:
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.apply_search_replace(str(guarded), target_text, "")

    @staticmethod
    def rename_symbol(path: str, old_name: str, new_name: str) -> dict:
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.rename_symbol_in_file(str(guarded), old_name, new_name)

    @staticmethod
    def find_symbol(project: str, symbol: str) -> dict:
        root, err = _guard(project, write=False)
        if err:
            return err
        if not root.is_dir():
            return {"status": "error", "message": f"Project not found: {project}"}
        finder = SymbolFinder(root)
        results = finder.find_symbol(symbol)
        return {"status": "success", "symbol": symbol, "definitions": results, "count": len(results)}

    @staticmethod
    def scan_storage(path: str = "", limit: int = 20) -> dict:
        """Read-only largest-files report inside the sandbox. Never deletes."""
        from core.fs_sandbox import scan_storage as _scan

        return _scan(path, limit=limit)

    @staticmethod
    def query_code_graph(project: str, query: str) -> dict:
        root, err = _guard(project, write=False)
        if err:
            return err
        from knowledge.graph_engine import GraphEngine
        try:
            ge = GraphEngine()
            graph_path = root / "graphify-out" / "graph.json"
            ge.load_graph(graph_path)
            if not ge.is_loaded():
                return {"status": "error", "message": f"Graph not generated for {project} yet. Open it first."}
            res = ge.query_relationships(query)
            return {"status": "success", "result": res}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @staticmethod
    def find_references(project: str, symbol: str) -> dict:
        root, err = _guard(project, write=False)
        if err:
            return err
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
        guarded, err = _guard(path, write=True)
        if err:
            return err
        from editing.file_editor import FileEditor
        return FileEditor.apply_unified_patch(str(guarded), patch)

    @staticmethod
    def validate_patch(path: str) -> dict:
        guarded, err = _guard(path, write=False)
        if err:
            return err
        from editing.patch_validator import PatchValidator
        result = PatchValidator.validate_patch(str(guarded))
        status = "success" if result["valid"] else "error"
        return {"status": status, **result}
