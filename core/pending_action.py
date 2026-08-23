APPROVAL_WORDS = frozenset({
    "yes", "y", "ok", "okay", "continue", "confirm", "proceed",
    # Natural spoken variants — still whole-utterance matches only
    "yeah", "yep", "yup", "sure", "do it", "go ahead", "affirmative",
})
REJECTION_WORDS = frozenset({
    "no", "n", "cancel", "stop", "reject", "deny",
    "nope", "nah", "don't", "dont", "negative", "abort",
})

# Tools that require user confirmation before execution.
CONFIRMATION_TOOLS = frozenset({
    "create_file",
    "write_file",
    "delete_file",
    "run_command",
    "scrape_page",
    "click_element",
    "fill_input",
    "kill_process",
    "edit_file",
    "insert_before",
    "insert_after",
    "replace_lines",
    "replace_regex",
    "append_file",
    "delete_block",
    "rename_symbol",
    "apply_patch",
    "git_add",
    "git_commit",
    "git_checkout",
    "git_switch",
    "git_fetch",
    "git_push",
    "git_reset",
    "git_stash",
    "docker_stop",
    "docker_rm",
    "db_execute",
})

# Explicit navigation / open — user asked; no confirmation.
DIRECT_BROWSER_TOOLS = frozenset({
    "open_url",
    "search_google",
    "open_application",
})

# Read-only tools that never require confirmation.
READ_ONLY_TOOLS = frozenset({
    "get_current_url",
    "get_page_title",
    "get_page_text",
    "list_tabs",
    "switch_tab",
    "read_file",
    "list_directory",
    "take_screenshot",
    "find_symbol",
    "query_code_graph",
    "find_references",
    "generate_diff",
    "validate_patch",
    "get_system_status",
    "list_top_processes",
    "web_search",
    "inspect_url",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "git_branch",
    "git_remote",
    "docker_ps",
    "docker_images",
    "docker_inspect",
    "docker_logs",
    "docker_info",
    "docker_version",
    "extract_document",
    "db_list_connections",
    "db_query",
    "db_mongo_find",
    "discover_tools",
    "DONE",
})

DANGEROUS_COMMAND_PATTERNS = (
    "install",
    "pip ",
    "npm ",
    "delete",
    "rm ",
    "del ",
    "move",
    "mv ",
    "uninstall",
    "format ",
    "rmdir",
    "rd ",
    "sudo ",
    "chmod ",
    "chown ",
    "shutdown",
    "reboot",
    "mkfs",
    "dd if=",
    "reg delete",
    "Remove-Item",
    "git reset --hard",
    "git push --force",
    "git push -f",
    "git branch -D",
    "git clean -f",
)


def _normalize_answer(text: str) -> str:
    """Strip speech punctuation so voice transcripts match cleanly."""
    return (text or "").strip().lower().rstrip(".!?,;: ")


def is_approval(text: str) -> bool:
    """Exact approval only — never treat 'yes fix that file' as confirm."""
    return _normalize_answer(text) in APPROVAL_WORDS


def is_rejection(text: str) -> bool:
    return _normalize_answer(text) in REJECTION_WORDS


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_destructive_invocation(tool_name: str, args: dict | None = None) -> bool:
    """True for irreversible / history-rewriting / remote-force operations."""
    args = args or {}
    if tool_name == "run_command":
        try:
            from tools.command_tool import CLASS_DESTRUCTIVE, classify_command

            return classify_command(str(args.get("cmd") or "")) == CLASS_DESTRUCTIVE
        except Exception:
            cmd = str(args.get("cmd") or "").lower()
            return any(p.lower() in cmd for p in DANGEROUS_COMMAND_PATTERNS[-5:])
    if tool_name == "git_reset":
        return str(args.get("mode") or "mixed").lower().lstrip("-") in {"hard", "keep"}
    if tool_name == "git_push":
        return _truthy(args.get("force"))
    if tool_name == "git_branch":
        return bool(args.get("delete"))
    if tool_name == "git_commit":
        return _truthy(args.get("amend"))
    if tool_name == "git_stash":
        return str(args.get("action") or "").lower() in {"drop", "clear"}
    if tool_name in {"docker_rm", "delete_file", "kill_process"}:
        return True
    if tool_name == "db_execute":
        sql = str(args.get("sql") or "").strip().split()
        return bool(sql) and sql[0].lower() in {"drop", "truncate", "alter"}
    return False


def with_user_confirmation(tool_name: str, args: dict | None = None) -> dict:
    """Mark a user-approved call so destructive primitives may proceed."""
    out = dict(args or {})
    if is_destructive_invocation(tool_name, out):
        out["confirm_destructive"] = True
    return out


def needs_confirmation(tool_name: str, args: dict | None = None) -> bool:
    """Return True if the tool call must be confirmed by the user first."""
    args = args or {}

    if is_destructive_invocation(tool_name, args):
        return True

    if tool_name == "git_stash":
        action = str(args.get("action") or "push").lower()
        if action in {"list", "show"}:
            return False
        return True

    if tool_name == "git_branch" and args.get("delete"):
        return True

    if tool_name in READ_ONLY_TOOLS or tool_name in DIRECT_BROWSER_TOOLS:
        return False
    if tool_name not in CONFIRMATION_TOOLS:
        # Unknown mutators default to confirm
        if tool_name and tool_name != "DONE":
            return True
        return False

    if tool_name == "run_command":
        cmd = args.get("cmd", "").lower()
        return bool(cmd.strip())

    return True


def format_confirmation_message(tool_name: str, args: dict) -> str:
    """Human-readable confirmation prompt for a pending action."""
    args = args or {}
    destructive = is_destructive_invocation(tool_name, args)
    prefix = "DESTRUCTIVE — this may be irreversible.\n" if destructive else ""
    if tool_name == "create_file":
        return f"I am about to create/overwrite:\n{args.get('path')}\nProceed? (yes/no)"
    if tool_name == "write_file":
        return f"I am about to write to:\n{args.get('path')}\nProceed? (yes/no)"
    if tool_name == "delete_file":
        return f"{prefix}I am about to delete:\n{args.get('path')}\nProceed? (yes/no)"
    if tool_name == "run_command":
        return f"{prefix}I am about to run this command:\n{args.get('cmd')}\nProceed? (yes/no)"
    if tool_name == "open_application":
        return f"I am about to open application:\n{args.get('name', args.get('app', 'unknown'))}\nProceed? (yes/no)"
    if tool_name == "kill_process":
        return f"{prefix}I am about to forcibly terminate process PID {args.get('pid')}.\nProceed? (yes/no)"
    if tool_name.startswith("git_"):
        return (
            f"{prefix}I am about to run git primitive `{tool_name}`:\n{args}\nProceed? (yes/no)"
        )
    if tool_name.startswith("docker_"):
        return (
            f"{prefix}I am about to run docker primitive `{tool_name}`:\n{args}\nProceed? (yes/no)"
        )
    if tool_name == "db_execute":
        return (
            f"{prefix}I am about to run SQL on connection `{args.get('connection')}`:\n"
            f"{args.get('sql')}\nProceed? (yes/no)"
        )
    if tool_name in {
        "edit_file", "insert_before", "insert_after", "replace_lines",
        "replace_regex", "append_file", "delete_block", "rename_symbol", "apply_patch",
    }:
        return f"I am about to modify:\n{args.get('path')}\nvia `{tool_name}`.\nProceed? (yes/no)"
    return f"{prefix}I am about to run tool '{tool_name}' with args {args}.\nProceed? (yes/no)"
