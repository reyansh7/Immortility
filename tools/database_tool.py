"""Configured-database primitives (SQLite / PostgreSQL / MongoDB).

The model may only use named connections from config/env. Arbitrary URLs are rejected.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.repo_paths import get_repo_root

_WRITE_VERBS = frozenset({
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "grant", "revoke", "attach", "detach", "vacuum", "reindex",
    "copy", "load", "merge", "call", "do", "comment",
})
_READ_VERBS = frozenset({"select", "with", "show", "explain", "describe", "pragma"})


def _fail(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error_code": code, "message": message}


def reset_db_config_cache() -> None:
    load_connections.cache_clear()


def _strip_sql_comments(sql: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    text = re.sub(r"--.*?$", " ", text, flags=re.M)
    return text.strip()


def classify_sql(sql: str) -> str:
    cleaned = _strip_sql_comments(sql or "")
    if not cleaned:
        return "empty"
    if ";" in cleaned.rstrip(";").strip():
        return "multi"
    first = cleaned.split()[0].lower() if cleaned.split() else ""
    if first in _WRITE_VERBS:
        return "write"
    if first == "pragma" and "=" in cleaned:
        return "write"
    if first in _READ_VERBS:
        return "read"
    return "unknown"


def _env_connection(name: str, db_type: str, **fields: str) -> dict[str, Any] | None:
    if not any(fields.values()):
        return None
    allow_raw = os.environ.get(f"IMMORTILITY_DB_{name.upper()}_ALLOW", "read")
    allow = [p.strip().lower() for p in allow_raw.split(",") if p.strip()]
    conn: dict[str, Any] = {"name": name, "type": db_type, "allow": allow or ["read"]}
    conn.update({k: v for k, v in fields.items() if v})
    return conn


@lru_cache(maxsize=1)
def load_connections() -> dict[str, dict[str, Any]]:
    """Named connections from config/databases.yaml plus env overlays."""
    connections: dict[str, dict[str, Any]] = {}
    path = get_repo_root() / "config" / "databases.yaml"
    if path.is_file():
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw = data.get("connections") or {}
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and item.get("name"):
                        connections[str(item["name"])] = dict(item)
            elif isinstance(raw, dict):
                for name, item in raw.items():
                    if isinstance(item, dict):
                        row = dict(item)
                        row["name"] = name
                        connections[str(name)] = row
        except Exception:
            pass

    sqlite_path = (os.environ.get("IMMORTILITY_SQLITE_PATH") or "").strip()
    if sqlite_path:
        connections["sqlite_default"] = {
            "name": "sqlite_default",
            "type": "sqlite",
            "path": sqlite_path,
            "allow": ["read", "write"]
            if os.environ.get("IMMORTILITY_SQLITE_ALLOW_WRITE", "").strip() in {"1", "true", "yes"}
            else ["read"],
        }
    pg_url = (os.environ.get("IMMORTILITY_POSTGRES_URL") or "").strip()
    if pg_url:
        connections["postgres_default"] = {
            "name": "postgres_default",
            "type": "postgres",
            "url": pg_url,
            "allow": ["read"],
        }
    mongo_url = (os.environ.get("IMMORTILITY_MONGO_URL") or "").strip()
    if mongo_url:
        connections["mongo_default"] = {
            "name": "mongo_default",
            "type": "mongo",
            "url": mongo_url,
            "allow": ["read"],
        }
    return connections


def _public_conn(conn: dict[str, Any]) -> dict[str, Any]:
    out = {
        "name": conn.get("name"),
        "type": conn.get("type"),
        "allow": list(conn.get("allow") or ["read"]),
    }
    if conn.get("path"):
        out["path"] = conn.get("path")
    if conn.get("database"):
        out["database"] = conn.get("database")
    # Never return raw URLs / passwords.
    if conn.get("url"):
        out["url_configured"] = True
    return out


def get_connection(name: str) -> dict[str, Any] | None:
    if not name:
        return None
    return load_connections().get(str(name))


def list_connections() -> dict[str, Any]:
    conns = [_public_conn(c) for c in load_connections().values()]
    return {"status": "success", "connections": conns, "count": len(conns)}


def _allows_write(conn: dict[str, Any]) -> bool:
    allow = {str(a).lower() for a in (conn.get("allow") or ["read"])}
    return "write" in allow


def _sqlite_connect(conn: dict[str, Any]):
    import sqlite3

    path = conn.get("path") or conn.get("database")
    if not path:
        raise ValueError("sqlite connection is missing path")
    db_path = Path(os.path.expanduser(str(path))).resolve()
    # Only configured path — do not create arbitrary new DBs unless the file exists
    # or write is allowed.
    if not db_path.is_file() and not _allows_write(conn):
        raise FileNotFoundError(f"SQLite file not found: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(db_path))


def _postgres_connect(conn: dict[str, Any]):
    url = conn.get("url") or conn.get("dsn")
    if not url:
        raise ValueError("postgres connection is missing url")
    try:
        import psycopg2
    except ImportError as exc:
        raise ImportError("psycopg2 is not installed. pip install psycopg2-binary") from exc
    return psycopg2.connect(url)


def _mongo_client(conn: dict[str, Any]):
    url = conn.get("url")
    if not url:
        raise ValueError("mongo connection is missing url")
    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise ImportError("pymongo is not installed. pip install pymongo") from exc
    return MongoClient(url, serverSelectionTimeoutMS=5000)


def _rows_from_cursor(cur, limit: int) -> tuple[list[str], list[list[Any]]]:
    cols = [d[0] for d in (cur.description or [])]
    rows: list[list[Any]] = []
    for i, row in enumerate(cur):
        if i >= limit:
            break
        rows.append([_jsonish(v) for v in row])
    return cols, rows


def _jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def db_query(
    connection: str,
    sql: str = "",
    max_rows: int = 200,
) -> dict[str, Any]:
    conn = get_connection(connection)
    if not conn:
        return _fail(
            "DB_NOT_CONFIGURED",
            f"Unknown connection '{connection}'. Use db_list_connections. "
            "Arbitrary database URLs are not allowed.",
        )
    kind = (conn.get("type") or "").lower()
    if kind in {"mongo", "mongodb"}:
        return _fail(
            "WRONG_TOOL",
            "MongoDB is not SQL. Use db_mongo_find on a configured mongo connection.",
        )
    kind_sql = classify_sql(sql)
    if kind_sql != "read":
        return _fail(
            "READ_ONLY_QUERY",
            f"db_query only runs read SQL (SELECT/WITH/EXPLAIN). Got: {kind_sql}. "
            "Use db_execute for writes on connections that allow write.",
        )
    try:
        n = max(1, min(int(max_rows), 1000))
    except (TypeError, ValueError):
        n = 200
    try:
        if kind == "sqlite":
            db = _sqlite_connect(conn)
            try:
                cur = db.execute(sql)
                cols, rows = _rows_from_cursor(cur, n)
            finally:
                db.close()
        elif kind in {"postgres", "postgresql"}:
            db = _postgres_connect(conn)
            try:
                cur = db.cursor()
                cur.execute(sql)
                cols, rows = _rows_from_cursor(cur, n)
            finally:
                db.close()
        else:
            return _fail("UNSUPPORTED_DB", f"Unsupported database type: {kind}")
    except ImportError as exc:
        return _fail("DRIVER_UNAVAILABLE", str(exc))
    except Exception as exc:
        return _fail("DB_QUERY_FAILED", str(exc))
    return {
        "status": "success",
        "connection": conn.get("name"),
        "type": kind,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
    }


def db_execute(
    connection: str,
    sql: str = "",
    confirm_destructive: bool = False,
) -> dict[str, Any]:
    conn = get_connection(connection)
    if not conn:
        return _fail(
            "DB_NOT_CONFIGURED",
            f"Unknown connection '{connection}'. Arbitrary database URLs are not allowed.",
        )
    if not _allows_write(conn):
        return _fail(
            "DB_WRITE_NOT_ALLOWED",
            f"Connection '{connection}' is read-only. Enable write in config/databases.yaml.",
        )
    kind_sql = classify_sql(sql)
    if kind_sql in {"empty", "unknown", "multi"}:
        return _fail("INVALID_SQL", f"Rejected SQL class: {kind_sql}")
    destructive = kind_sql == "write" and _strip_sql_comments(sql).split()[0].lower() in {
        "drop", "truncate", "alter",
    }
    if destructive and str(confirm_destructive).lower() not in {"true", "1", "yes", "on"}:
        return _fail(
            "DESTRUCTIVE_CONFIRMATION_REQUIRED",
            "DROP/TRUNCATE/ALTER never run silently. Confirm, then retry with confirm_destructive=true.",
        )
    kind = (conn.get("type") or "").lower()
    try:
        if kind == "sqlite":
            db = _sqlite_connect(conn)
            try:
                cur = db.execute(sql)
                db.commit()
                return {
                    "status": "success",
                    "connection": conn.get("name"),
                    "rowcount": cur.rowcount,
                }
            finally:
                db.close()
        if kind in {"postgres", "postgresql"}:
            db = _postgres_connect(conn)
            try:
                cur = db.cursor()
                cur.execute(sql)
                db.commit()
                return {
                    "status": "success",
                    "connection": conn.get("name"),
                    "rowcount": cur.rowcount,
                }
            finally:
                db.close()
        return _fail("UNSUPPORTED_DB", f"Unsupported database type: {kind}")
    except ImportError as exc:
        return _fail("DRIVER_UNAVAILABLE", str(exc))
    except Exception as exc:
        return _fail("DB_EXECUTE_FAILED", str(exc))


def db_mongo_find(
    connection: str,
    collection: str = "",
    filter_json: str = "{}",
    database: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    conn = get_connection(connection)
    if not conn:
        return _fail(
            "DB_NOT_CONFIGURED",
            f"Unknown connection '{connection}'. Arbitrary Mongo URLs are not allowed.",
        )
    kind = (conn.get("type") or "").lower()
    if kind not in {"mongo", "mongodb"}:
        return _fail("WRONG_TOOL", f"Connection '{connection}' is {kind}, not mongo.")
    if not collection:
        return _fail("INVALID_ARGS", "db_mongo_find requires a collection name.")
    try:
        query = json.loads(filter_json or "{}")
        if not isinstance(query, dict):
            return _fail("INVALID_ARGS", "filter_json must be a JSON object.")
    except json.JSONDecodeError as exc:
        return _fail("INVALID_ARGS", f"filter_json is not valid JSON: {exc}")
    try:
        n = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        n = 50
    try:
        client = _mongo_client(conn)
        try:
            db_name = database or conn.get("database") or "admin"
            coll = client[db_name][collection]
            docs = []
            for doc in coll.find(query).limit(n):
                doc.pop("_id", None) if False else None
                docs.append(_jsonish_doc(doc))
        finally:
            client.close()
    except ImportError as exc:
        return _fail("DRIVER_UNAVAILABLE", str(exc))
    except Exception as exc:
        return _fail("DB_QUERY_FAILED", str(exc))
    return {
        "status": "success",
        "connection": conn.get("name"),
        "database": database or conn.get("database"),
        "collection": collection,
        "documents": docs,
        "count": len(docs),
    }


def _jsonish_doc(doc: Any) -> Any:
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k == "_id":
                out[k] = str(v)
            else:
                out[k] = _jsonish_doc(v)
        return out
    if isinstance(doc, list):
        return [_jsonish_doc(x) for x in doc]
    return _jsonish(doc)
