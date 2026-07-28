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
)


def _normalize_answer(text: str) -> str:
    """Strip speech punctuation so voice transcripts match cleanly."""
    return (text or "").strip().lower().rstrip(".!?,;: ")


def is_approval(text: str) -> bool:
    """Exact approval only — never treat 'yes fix that file' as confirm."""
    return _normalize_answer(text) in APPROVAL_WORDS


def is_rejection(text: str) -> bool:
    return _normalize_answer(text) in REJECTION_WORDS


def needs_confirmation(tool_name: str, args: dict | None = None) -> bool:
    """Return True if the tool call must be confirmed by the user first."""
    if tool_name in READ_ONLY_TOOLS or tool_name in DIRECT_BROWSER_TOOLS:
        return False
    if tool_name not in CONFIRMATION_TOOLS:
        # Unknown mutators default to confirm
        if tool_name and tool_name != "DONE":
            return True
        return False

    args = args or {}

    if tool_name == "run_command":
        cmd = args.get("cmd", "").lower()
        return bool(cmd.strip())

    return True


def format_confirmation_message(tool_name: str, args: dict) -> str:
    """Human-readable confirmation prompt for a pending action."""
    if tool_name == "create_file":
        return f"I am about to create/overwrite:\n{args.get('path')}\nProceed? (yes/no)"
    if tool_name == "write_file":
        return f"I am about to write to:\n{args.get('path')}\nProceed? (yes/no)"
    if tool_name == "delete_file":
        return f"I am about to delete:\n{args.get('path')}\nProceed? (yes/no)"
    if tool_name == "run_command":
        return f"I am about to run this command:\n{args.get('cmd')}\nProceed? (yes/no)"
    if tool_name == "open_url":
        return f"I am about to open:\n{args.get('url')}\nProceed? (yes/no)"
    if tool_name == "search_google":
        return f"I am about to search for:\n{args.get('query')}\nProceed? (yes/no)"
    if tool_name == "open_application":
        return f"I am about to open application:\n{args.get('name', args.get('app', 'unknown'))}\nProceed? (yes/no)"
    if tool_name == "kill_process":
        return f"I am about to forcibly terminate process PID {args.get('pid')}.\nProceed? (yes/no)"
    if tool_name in {
        "edit_file", "insert_before", "insert_after", "replace_lines",
        "replace_regex", "append_file", "delete_block", "rename_symbol", "apply_patch",
    }:
        return f"I am about to modify:\n{args.get('path')}\nvia `{tool_name}`.\nProceed? (yes/no)"
    return f"I am about to run tool '{tool_name}' with args {args}.\nProceed? (yes/no)"
