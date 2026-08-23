"""CLI slash commands must not fall through to the Action Engine."""

from __future__ import annotations

from main import (
    is_import_docs_command,
    parse_import_docs_args,
    unknown_slash_command,
)


def test_bare_import_docs_is_a_cli_command():
    assert is_import_docs_command("/import-docs")
    assert is_import_docs_command("/import-docs fastapi C:\\\\docs")
    assert is_import_docs_command("  /IMPORT-DOCS  ")
    assert not is_import_docs_command("please run import-docs")
    assert parse_import_docs_args("/import-docs") is None
    parsed = parse_import_docs_args('/import-docs fastapi C:\\docs\\fastapi')
    assert parsed == ("fastapi", "C:\\docs\\fastapi")


def test_unknown_slash_does_not_look_like_agent_work():
    msg = unknown_slash_command("/import-docs")
    assert msg is None
    msg = unknown_slash_command("/not-a-real-command")
    assert msg is not None
    assert "Unknown command" in msg
    assert unknown_slash_command("analyze the repo") is None
