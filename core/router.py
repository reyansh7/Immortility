import asyncio
import re

from ollama import chat

VALID_CATEGORIES = ("RESEARCH_TASK", "ACTION", "TASK", "CHAT", "PROJECT")


from core.project_extract import is_run_project_request


def _fast_route(user_input: str) -> str | None:
    """Regex fast-path — skip LLM for obvious cases."""
    lower = user_input.strip().lower()
    if lower in ("hi", "hello", "hey", "thanks", "thank you"):
        return "CHAT"
    if lower in ("yes", "y", "ok", "proceed", "continue", "confirm"):
        return "PROJECT"
    if is_run_project_request(user_input):
        return "PROJECT"
    if lower.startswith("/open") or "open my project" in lower or "open project" in lower:
        return "PROJECT"
    if lower.startswith("where is") or lower.startswith("find ") or "explain my" in lower:
        return "PROJECT"
    if any(kw in lower for kw in (
        "implement", "fix ", "refactor", "rename", "jwt", "make the changes",
        "debug", "error", "create file", "create component", "build project", "add ", "login", "navbar",
        "authentication", "make these changes", "404",
    )):
        return "PROJECT"
    if lower.startswith("open ") and "http" not in lower:
        return "ACTION"
    if any(kw in lower for kw in ("run the", "start the", "launch the", "run my")):
        return "ACTION"
    if "summarize" in lower and "page" in lower:
        return "ACTION"
    return None


async def classify_route(user_input: str, routing_context: str = "") -> str:
    """Classify user input from prompt + retrieved context."""

    prompt = f"""You are the Intent Router.
First, read and reason over the Retrieved context.
Then classify into exactly ONE category and reply with ONLY the category name.

CHAT | ACTION | TASK | RESEARCH_TASK | PROJECT

Input: {user_input}
Retrieved context (read-only):
{routing_context[:2500] if routing_context else "None"}
Category:"""

    try:
        response = await asyncio.to_thread(
            chat,
            model="qwen3:8b",
            messages=[{"role": "user", "content": prompt}],
            think=False,
        )
        category = response["message"]["content"].strip().upper()
    except Exception:
        return "CHAT"

    token = re.split(r"[\s\n,.]+", category)[0]
    if token in VALID_CATEGORIES:
        return token

    for valid in sorted(VALID_CATEGORIES, key=len, reverse=True):
        if category.startswith(valid):
            return valid

    return "CHAT"
