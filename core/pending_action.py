APPROVAL_WORDS = frozenset({"yes", "y", "ok", "continue", "confirm", "proceed"})
REJECTION_WORDS = frozenset({"no", "n", "cancel", "stop"})

# Tools that require user confirmation before execution.
CONFIRMATION_TOOLS = frozenset({
    "create_file",
    "write_file",
    "delete_file",
    "run_command",
    "scrape_page",
    "click_element",
    "fill_input",
    "open_application",
    "kill_process",
})

# Direct navigation/search — user explicitly requested; no confirmation needed.
DIRECT_BROWSER_TOOLS = frozenset({
    "open_url",
    "search_google",
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
)


def is_approval(text: str) -> bool:
    lower = text.strip().lower()
    if lower in APPROVAL_WORDS:
        return True
    
    # "yes make the changes", "yes, proceed"
    # But NOT "ok 1st list all folders in projects folder"
    words = lower.replace(",", " ").split()
    if not words:
        return False
        
    first = words[0]
    return first in APPROVAL_WORDS and len(words) <= 5


def is_rejection(text: str) -> bool:
    return text.strip().lower() in REJECTION_WORDS


def needs_confirmation(tool_name: str, args: dict | None = None) -> bool:
    """Return True if the tool call must be confirmed by the user first."""
    if tool_name in READ_ONLY_TOOLS or tool_name in DIRECT_BROWSER_TOOLS:
        return False
    if tool_name not in CONFIRMATION_TOOLS:
        return False

    args = args or {}

    if tool_name == "run_command":
        cmd = args.get("cmd", "").lower()
        return any(pattern in cmd for pattern in DANGEROUS_COMMAND_PATTERNS) or bool(cmd.strip())

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
    return f"I am about to run tool '{tool_name}' with args {args}.\nProceed? (yes/no)"
