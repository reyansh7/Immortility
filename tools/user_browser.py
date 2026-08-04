"""Open URLs in the user's real Google Chrome profile (logged-in account).

``webbrowser.open`` often hits Edge, a guest Chrome, or a fresh profile.
This module launches ``chrome.exe`` with ``--profile-directory=…`` so
YouTube / Google / Netflix keep Reyansh's session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)

# Site home pages
SITE_HOME: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "netflix": "https://www.netflix.com",
    "leetcode": "https://leetcode.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "gemini": "https://gemini.google.com",
    "chatgpt": "https://chatgpt.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "reddit": "https://www.reddit.com",
    "spotify": "https://open.spotify.com",
    "discord": "https://discord.com/app",
    "whatsapp": "https://web.whatsapp.com",
    "linkedin": "https://www.linkedin.com",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
    "drive": "https://drive.google.com",
    "docs": "https://docs.google.com",
    "notion": "https://www.notion.so",
    "figma": "https://www.figma.com",
    "amazon": "https://www.amazon.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "twitch": "https://www.twitch.tv",
    "hulu": "https://www.hulu.com",
    "prime": "https://www.primevideo.com",
    "prime video": "https://www.primevideo.com",
    "wikipedia": "https://en.wikipedia.org",
    "wiki": "https://en.wikipedia.org",
}

_SEARCH_ENGINES = {
    "youtube", "yt", "google", "wikipedia", "wiki",
    "leetcode", "github", "reddit", "netflix", "spotify",
}

# Common misspellings → canonical site key / verb
_TYPOS: dict[str, str] = {
    "youttube": "youtube",
    "youtub": "youtube",
    "yotube": "youtube",
    "you tube": "youtube",
    "ytube": "youtube",
    "netlfix": "netflix",
    "leetcod": "leetcode",
    "leet code": "leetcode",
    "googel": "google",
    "gogle": "google",
    "wikipeda": "wikipedia",
    "wikipidia": "wikipedia",
    "searrch": "search",
    "serach": "search",
    "serch": "search",
    "saerch": "search",
    "searh": "search",
    "searchh": "search",
}

_MEDIA_HINTS = re.compile(
    r"\b("
    r"vines?|video|videos|song|songs|music|trailer|trailers|episode|episodes|"
    r"movie|movies|clip|clips|standup|stand[\s-]?up|comedy|podcast|anime|"
    r"remix|mv|official|live\s+set|concert"
    r")\b",
    re.I,
)

_LOCAL_NOT_WEB = re.compile(
    r"\b("
    r"code|file|folder|project|desktop|repo|readme|function|class|bug|"
    r"error|terminal|vscode|immortility|stocks_app"
    r")\b",
    re.I,
)


def find_chrome_exe() -> Path | None:
    env = (os.environ.get("IMMORTILITY_CHROME_PATH") or "").strip()
    if env and Path(env).is_file():
        return Path(env)
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    for p in (
        Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ):
        if p.is_file():
            return p
    return None


def chrome_user_data_dir() -> Path:
    override = (os.environ.get("IMMORTILITY_CHROME_USER_DATA") or "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / "Google" / "Chrome" / "User Data"


def resolve_chrome_profile() -> str:
    """Prefer env override, else last-used profile from Chrome Local State."""
    override = (os.environ.get("IMMORTILITY_CHROME_PROFILE") or "").strip()
    if override:
        return override

    local_state = chrome_user_data_dir() / "Local State"
    try:
        data = json.loads(local_state.read_text(encoding="utf-8"))
        info = data.get("profile") or {}
        last = info.get("last_used")
        if isinstance(last, str) and last:
            return last
        profiles = info.get("info_cache") or {}
        for name, meta in profiles.items():
            if not isinstance(meta, dict):
                continue
            if meta.get("is_ephemeral") or name.lower().startswith("guest"):
                continue
            if meta.get("user_name") or meta.get("gaia_name") or meta.get("name"):
                return name
    except Exception as exc:
        logger.debug("Chrome Local State read failed: %s", exc)
    return "Default"


def open_urls_in_user_chrome(urls: list[str]) -> tuple[list[str], str]:
    """Open URLs in the user's Chrome profile. Returns (opened, note)."""
    chrome = find_chrome_exe()
    if not chrome:
        import webbrowser

        opened: list[str] = []
        for url in urls:
            try:
                webbrowser.open(url, new=2)
                opened.append(url)
            except Exception as exc:
                logger.warning("webbrowser.open failed %s: %s", url, exc)
        return opened, "default browser (Chrome not found)"

    profile = resolve_chrome_profile()
    opened = []
    for url in urls:
        try:
            subprocess.Popen(
                [
                    str(chrome),
                    f"--profile-directory={profile}",
                    "--new-tab",
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            opened.append(url)
        except Exception as exc:
            logger.warning("Chrome launch failed for %s: %s", url, exc)
    return opened, f"Chrome profile '{profile}'"


def _fix_typos(text: str) -> str:
    low = text.lower()
    for typo, canon in sorted(_TYPOS.items(), key=lambda x: -len(x[0])):
        low = re.sub(rf"(?<![a-z]){re.escape(typo)}(?![a-z])", canon, low)
    low = re.sub(r"\bsear+ch\b", "search", low)
    return low


def _canon_engine(name: str) -> str:
    n = (name or "").lower().strip()
    return {"yt": "youtube", "wiki": "wikipedia"}.get(n, n)


def _search_url(engine: str, query: str) -> str:
    q = urllib.parse.quote_plus(query.strip())
    engine = _canon_engine(engine)
    if engine == "youtube":
        return f"https://www.youtube.com/results?search_query={q}"
    if engine == "wikipedia":
        return f"https://en.wikipedia.org/wiki/Special:Search?search={q}"
    if engine == "leetcode":
        return f"https://leetcode.com/problemset/?search={q}"
    if engine == "github":
        return f"https://github.com/search?q={q}"
    if engine == "reddit":
        return f"https://www.reddit.com/search/?q={q}"
    if engine == "netflix":
        return f"https://www.netflix.com/search?q={q}"
    if engine == "spotify":
        return f"https://open.spotify.com/search/{q}"
    return f"https://www.google.com/search?q={q}"


def _clean_query(q: str) -> str:
    q = (q or "").strip(" \t\"'`.,:;!?")
    q = re.sub(
        r"\b(on|in|with|using|via)\s+"
        r"(youtube|yt|google|netflix|wikipedia|wiki|chrome|browser)\b",
        "",
        q,
        flags=re.I,
    )
    q = re.sub(
        r"\b(please|for\s+me|now|thanks|thank\s+you)\b",
        "",
        q,
        flags=re.I,
    )
    q = re.sub(r"\s+", " ", q).strip(" \t\"'`.,:;!?")
    return q


def _extract_search_intents(low: str) -> list[tuple[str, str]]:
    """Return (engine, query) pairs from natural language."""
    hits: list[tuple[str, str]] = []

    patterns = [
        # open youtube and search <q>  |  youtube search <q>
        (
            r"(?:open|launch|go\s+to|pull\s+up)?\s*"
            r"(youtube|yt|google|netflix|wikipedia|wiki|github|reddit|spotify)\s+"
            r"(?:and\s+|then\s+|,\s*)?"
            r"(?:search|find|look\s*up|show|play)\s+(?:for\s+|me\s+)?"
            r"(.+)$"
        ),
        # search/find <q> on/in youtube
        (
            r"(?:search|find|look\s*up|google)\s+(?:for\s+)?"
            r"(.+?)\s+(?:on|in|via|using)\s+"
            r"(youtube|yt|google|wikipedia|wiki|leetcode|github|reddit|netflix|spotify)\b"
        ),
        # youtube search <q>
        (
            r"\b(youtube|yt|google|wikipedia|wiki|leetcode|github|reddit|netflix|spotify)\s+"
            r"(?:search|find)\s+(?:for\s+)?(.+)$"
        ),
        # search youtube for <q>
        (
            r"(?:search|find)\s+(?:on\s+)?"
            r"(youtube|yt|google|wikipedia|wiki|leetcode|github|reddit|netflix|spotify)\s+"
            r"(?:for\s+)?(.+)$"
        ),
        # open/play/watch/see <q> on/in youtube
        (
            r"(?:open|play|watch|see|show(?:\s+me)?|pull\s+up|stream)\s+"
            r"(.+?)\s+(?:on|in|via|using)\s+"
            r"(youtube|yt|netflix|spotify|google|wikipedia|wiki|github|reddit)\b"
        ),
        # i want to see|watch <q> on youtube
        (
            r"(?:i\s+)?(?:want\s+to|wanna|would\s+like\s+to)\s+"
            r"(?:see|watch|play|listen\s+to|find|search(?:\s+for)?)\s+"
            r"(.+?)\s+(?:on|in)\s+"
            r"(youtube|yt|netflix|spotify|google)\b"
        ),
        # i want to see|watch <q>  (default YouTube for media / short queries)
        (
            r"(?:i\s+)?(?:want\s+to|wanna|would\s+like\s+to)\s+"
            r"(?:see|watch|play|listen\s+to)\s+(.+)$"
        ),
        r"^\s*(?:watch|play|stream)\s+(.+)$",
        r"^\s*(?:show\s+me|put\s+on)\s+(.+)$",
        r"^\s*google\s+(.+)$",
        r"^\s*(?:wikipedia|wiki)\s+(.+)$",
        r"^\s*(?:search|find|look\s*up)\s+(?:for\s+)?(.+)$",
    ]

    for pat in patterns:
        m = re.search(pat, low, flags=re.I)
        if not m:
            continue
        groups = [g.strip() for g in m.groups() if g and g.strip()]
        if not groups:
            continue

        if len(groups) == 1:
            query = _clean_query(groups[0])
            if not query or len(query) < 2:
                continue
            if pat.startswith(r"^\s*google"):
                hits.append(("google", query))
                break
            if pat.startswith(r"^\s*(?:wikipedia|wiki)"):
                hits.append(("wikipedia", query))
                break
            if pat.startswith(r"^\s*(?:search|find|look"):
                hits.append(("google", query))
                break
            if _LOCAL_NOT_WEB.search(query):
                continue
            # watch / see / play / show → YouTube for entertainment queries
            if (
                _MEDIA_HINTS.search(query)
                or re.search(r"(?:see|watch|play|stream|show|put\s+on)", pat)
                or len(query.split()) <= 6
            ):
                hits.append(("youtube", query))
                break
            continue

        if len(groups) >= 2:
            a, b = groups[0], groups[1]
            if a.lower() in _SEARCH_ENGINES:
                eng, query = a, b
            elif b.lower() in _SEARCH_ENGINES:
                eng, query = b, a
            else:
                eng, query = "google", f"{a} {b}"
            query = _clean_query(query)
            if not query or query.lower() in _SEARCH_ENGINES:
                continue
            if query.lower() in {"it", "that", "this", "them", "something"}:
                continue
            hits.append((_canon_engine(eng), query))
            break

    return hits


def extract_browser_targets(message: str) -> list[str]:
    """Parse open/search intents into concrete URLs (order preserved)."""
    raw = (message or "").strip()
    if not raw:
        return []
    low = _fix_typos(raw)
    urls: list[str] = []

    for m in re.finditer(r"https?://[^\s<>\"']+", raw, flags=re.I):
        urls.append(m.group(0).rstrip(".,);]"))

    if re.search(r"\bopen\s+(it|that|them|this)\s+(in|on|with)\s+(google|chrome)\b", low):
        urls.append("https://www.google.com")
        return list(dict.fromkeys(urls))

    search_engines_hit: set[str] = set()
    for eng, query in _extract_search_intents(low):
        urls.append(_search_url(eng, query))
        search_engines_hit.add(_canon_engine(eng))

    # Search wins — do not also open the site homepage
    if search_engines_hit:
        out: list[str] = []
        for u in urls:
            if u not in out:
                out.append(u)
        return out

    cleaned = re.sub(
        r"\b(on|in|with|using)\s+(google\s+chrome|chrome|brave|edge|browser|my\s+chrome)\b",
        " ",
        low,
    )
    wants_open = any(
        w in cleaned
        for w in (
            "open ", "launch ", "go to ", "navigate ", "browse ",
            "take me to", "pull up ", "show me ", "visit ",
            "watch ", "play ", "stream ", "see ",
        )
    ) or cleaned.startswith(("open", "launch", "watch", "play", "search", "find"))

    if wants_open or urls:
        mentioned: list[tuple[int, str, str]] = []
        for name in SITE_HOME.keys():
            m = re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", cleaned)
            if not m:
                continue
            canon = _canon_engine(name)
            if canon in search_engines_hit or name in search_engines_hit:
                continue
            if name == "google" and re.search(r"\b(in|on|with|using)\s+google\b", low) and not re.search(
                r"\b(open|go to|visit)\s+google\b", low
            ):
                continue
            mentioned.append((m.start(), name, SITE_HOME[name]))
        mentioned.sort(key=lambda t: t[0])
        for _, _, home in mentioned:
            if home not in urls:
                urls.append(home)

    if wants_open:
        for m in re.finditer(r"\b([a-z0-9-]+\.(?:com|org|net|io|dev|ai))\b", cleaned):
            u = "https://" + m.group(1)
            if u not in urls:
                urls.append(u)

    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


def wants_browser_action(message: str) -> bool:
    """True when the message is primarily about opening / searching the web."""
    low = _fix_typos((message or "").lower())
    if not low.strip():
        return False
    if re.search(r"\bopen\s+(it|that|them|this)\s+(in|on|with)\s+(google|chrome)\b", low):
        return True
    if extract_browser_targets(message):
        if re.search(
            r"\b(solve|implement|write\s+code|fix\s+bug|refactor)\b", low
        ) and not re.search(
            r"\b(open|search|google|browse|go to|visit|watch|play|see)\b", low
        ):
            return False
        return True
    if re.search(
        r"\b(search|google|browse|youtube|wikipedia|watch|play)\b", low
    ) and re.search(r"\b(for|on|about|see|want)\b", low):
        return True
    if re.search(r"(?:i\s+)?(?:want\s+to|wanna)\s+(?:see|watch|play|search)", low):
        return True
    return False


def handle_browser_request(message: str) -> str | None:
    """Open sites/searches in the user's Chrome. Returns reply or None."""
    targets = extract_browser_targets(message)
    if not targets:
        return None
    opened, note = open_urls_in_user_chrome(targets)
    if not opened:
        return "I couldn't open those pages in Chrome."
    pretty: list[str] = []
    for u in opened:
        parsed = urllib.parse.urlparse(u)
        qs = urllib.parse.parse_qs(parsed.query)
        if "youtube.com/results" in u:
            q = (qs.get("search_query") or [""])[0]
            pretty.append(f"YouTube search for '{q}'" if q else u)
        elif "google.com/search" in u:
            q = (qs.get("q") or [""])[0]
            pretty.append(f"Google search for '{q}'" if q else u)
        else:
            pretty.append(u)
    return f"Opened in your {note}: {', '.join(pretty)}."
