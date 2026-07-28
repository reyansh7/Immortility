"""Domain vocabulary for voice chat: Whisper biasing + transcript repair.

Whisper mishears domain words it has never been primed for ("Immortility"
becomes "immortality", "LeetCode" becomes "leet code"). Two defences:

1. ``hotwords_string`` / ``initial_prompt`` bias decoding *before* it happens.
2. ``repair_transcript`` fixes what still slips through, conservatively.
"""

from __future__ import annotations

import difflib
import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# Canonical spellings Immortility should always produce.
BRAND_TERMS: tuple[str, ...] = (
    "Immortility",
    "Reyansh",
    "YouTube",
    "Netflix",
    "LeetCode",
    "GitHub",
    "Wikipedia",
    "Gmail",
    "ChatGPT",
    "Spotify",
    "Discord",
    "WhatsApp",
    "LinkedIn",
    "Instagram",
    "Twitch",
    "Reddit",
    "Google",
    "Chrome",
    "Ollama",
    "Qwen",
    "Whisper",
    "Chroma",
    "Notion",
    "Figma",
    "Amazon",
)

# Words that steer commands. Mishearing these breaks routing.
COMMAND_TERMS: tuple[str, ...] = (
    "open",
    "launch",
    "search",
    "create",
    "folder",
    "file",
    "desktop",
    "project",
    "projects",
    "analyze",
    "index",
    "knowledge",
    "vector",
    "database",
    "memory",
    "code",
    "write",
    "read",
    "delete",
    "rename",
    "screenshot",
    "volume",
    "brightness",
    "python",
    "terminal",
    "browser",
    "solution",
    "problem",
    "repository",
)

# Multi-word repairs applied before token-level fuzzy matching.
PHRASE_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\byou\s*tube\b", re.I), "YouTube"),
    (re.compile(r"\bleet\s*code\b", re.I), "LeetCode"),
    (re.compile(r"\blead\s*code\b", re.I), "LeetCode"),
    (re.compile(r"\bstack\s*overflow\b", re.I), "Stack Overflow"),
    (re.compile(r"\bgit\s*hub\b", re.I), "GitHub"),
    (re.compile(r"\bchat\s*g\.?\s*p\.?\s*t\.?\b", re.I), "ChatGPT"),
    (re.compile(r"\bvector\s+data\s*base\b", re.I), "vector database"),
    (re.compile(r"\bwhats\s*app\b", re.I), "WhatsApp"),
    (re.compile(r"\bprime\s*video\b", re.I), "Prime Video"),
    (re.compile(r"\bnet\s*flix\b", re.I), "Netflix"),
    (re.compile(r"\bimmortal\s*ity\b", re.I), "Immortility"),
    (re.compile(r"\bim\s*mortality\b", re.I), "Immortility"),
    (re.compile(r"\brag\s+code\b", re.I), "RAG code"),
    (re.compile(r"\bvs\s*code\b", re.I), "VS Code"),
    (re.compile(r"\bnew\s+fold\b", re.I), "new folder"),
)

# High-frequency single-word mishears that fuzzy matching scores too low.
ALIASES: dict[str, str] = {
    "netflicks": "Netflix",
    "netflex": "Netflix",
    "netflick": "Netflix",
    "utube": "YouTube",
    "youtub": "YouTube",
    "youtube": "YouTube",
    "yotube": "YouTube",
    "youttube": "YouTube",
    "wikipeda": "Wikipedia",
    "wikipidia": "Wikipedia",
    "wikapedia": "Wikipedia",
    "immortality": "Immortility",
    "immortaility": "Immortility",
    "immortelity": "Immortility",
    "mortality": "Immortility",
    "leetcode": "LeetCode",
    "leetcod": "LeetCode",
    "reyansh": "Reyansh",
    "riyansh": "Reyansh",
    "rayansh": "Reyansh",
    "ryansh": "Reyansh",
    "olama": "Ollama",
    "oyama": "Ollama",
    "quen": "Qwen",
    "gethub": "GitHub",
    "guthub": "GitHub",
}

# Frequent English words that must never be "corrected" into a domain term.
_PROTECTED_WORDS = frozenset(
    """
    a an and are as at be but by can could did do does for from get go had has have
    he her him his how i if in into is it its just like me my no not now of on one
    or our out say she should so some tell than that the their them then there these
    they this to too up us was we were what when where which who why will with would
    you your yes yeah ok okay okay. please thanks thank stop wait also next again
    make made made more most much new old only other over same see show still such
    take talk than time use very want way well were work write wrote here hear about
    after all any because been before being below between both down during each few
    further had having if into itself more once other own through under until while
    """.split()
)


@lru_cache(maxsize=1)
def _canonical_map() -> dict[str, str]:
    """lowercase form -> canonical spelling."""
    out: dict[str, str] = {}
    for term in BRAND_TERMS:
        out[term.lower()] = term
    for term in COMMAND_TERMS:
        out.setdefault(term.lower(), term)
    for name in _project_names():
        out.setdefault(name.lower(), name)
    return out


@lru_cache(maxsize=1)
def _project_names() -> tuple[str, ...]:
    """Real folder names on this machine, so 'SkillLens' is transcribed right."""
    try:
        from core.desktop_scanner import list_desktop_top_folders, list_project_folders

        names = list(list_project_folders()) + list(list_desktop_top_folders())
    except Exception as exc:
        logger.debug("voice vocab project names unavailable: %s", exc)
        return ()
    # Single-token, alphabetic-ish names only — multiword folders confuse decoding
    cleaned = [
        n
        for n in names
        if n and " " not in n and len(n) >= 4 and re.fullmatch(r"[A-Za-z0-9._-]+", n)
    ]
    return tuple(dict.fromkeys(cleaned))[:24]


@lru_cache(maxsize=1)
def hotwords_string() -> str:
    """Space-joined bias terms for faster-whisper ``hotwords``."""
    terms = list(BRAND_TERMS) + list(_project_names())
    return " ".join(terms)


@lru_cache(maxsize=1)
def initial_prompt() -> str:
    """Short priming sentence. Whisper truncates long prompts, so stay tight."""
    projects = ", ".join(_project_names()[:5])
    base = (
        "Immortility assistant for Reyansh. "
        "Open YouTube, Netflix, LeetCode, GitHub, Wikipedia. "
        "Create a folder on Desktop. Search on Google."
    )
    if projects:
        base += f" Projects: {projects}."
    if len(base) <= 224:
        return base
    # Never truncate mid-word — a half token biases decoding badly
    return base[:224].rsplit(" ", 1)[0].rstrip(",") + "."


def _repair_token(token: str) -> str:
    """Fuzzy-map one word onto the domain vocabulary, or leave it alone."""
    low = token.lower()
    canon = _canonical_map()

    if low in ALIASES:
        return ALIASES[low]
    if low in canon:
        replacement = canon[low]
        # Only re-case brands with internal capitals (YouTube, LeetCode).
        # Plain command words keep Whisper's sentence casing.
        return replacement if replacement.lower() != replacement else token
    if low in _PROTECTED_WORDS or len(low) < 4:
        return token

    # Only consider candidates of similar length; keeps "cold" from -> "code"
    candidates = [k for k in canon if abs(len(k) - len(low)) <= 2]
    match = difflib.get_close_matches(low, candidates, n=1, cutoff=0.86)
    if not match:
        return token
    replacement = canon[match[0]]
    logger.debug("voice vocab repair %r -> %r", token, replacement)
    return replacement


def repair_transcript(text: str) -> str:
    """Normalize domain words in a Whisper transcript. Never changes meaning."""
    raw = (text or "").strip()
    if not raw:
        return ""

    out = raw
    for pattern, repl in PHRASE_FIXES:
        out = pattern.sub(repl, out)

    # Token-level pass, preserving punctuation and spacing
    def _sub(match: re.Match[str]) -> str:
        return _repair_token(match.group(0))

    out = re.sub(r"[A-Za-z][A-Za-z'-]*", _sub, out)
    return re.sub(r"\s+", " ", out).strip()
