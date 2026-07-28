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

# Common misspellings → canonical site key
_TYPOS: dict[str, str] = {
    "youttube": "youtube",
    "youtub": "youtube",
    "yotube": "youtube",
    "you tube": "youtube",
    "ytube": "youtube",    "netlfix": "netflix",
    "leetcod": "leetcode",
    "leet code": "leetcode",
    "googel": "google",
    "gogle": "google",
    "wikipeda": "wikipedia",
    "wikipidia": "wikipedia",
}


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
        # fall back to first non-Guest profile with an email / name
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
        # Fallback: OS default handler
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
            # Do NOT pass --user-data-dir when Chrome is already running —
            # profile-directory alone reuses the logged-in session.
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
    return low


def _search_url(engine: str, query: str) -> str:
    q = urllib.parse.quote_plus(query.strip())
    engine = engine.lower().strip()
    if engine in {"youtube", "yt"}:
        return f"https://www.youtube.com/results?search_query={q}"
    if engine in {"wikipedia", "wiki"}:
        return f"https://en.wikipedia.org/wiki/Special:Search?search={q}"
    if engine == "leetcode":
        return f"https://leetcode.com/problemset/?search={q}"
    if engine == "github":
        return f"https://github.com/search?q={q}"
    if engine == "reddit":
        return f"https://www.reddit.com/search/?q={q}"
    if engine == "netflix":
        return f"https://www.netflix.com/search?q={q}"
    # google / gemini / default web search
    return f"https://www.google.com/search?q={q}"


def extract_browser_targets(message: str) -> list[str]:
    """Parse open/search intents into concrete URLs (order preserved)."""
    raw = (message or "").strip()
    if not raw:
        return []
    low = _fix_typos(raw)
    urls: list[str] = []

    # Explicit http(s) links
    for m in re.finditer(r"https?://[^\s<>\"']+", raw, flags=re.I):
        urls.append(m.group(0).rstrip(".,);]"))

    # "open it in google/chrome" → open Google in the user's Chrome
    if re.search(r"\bopen\s+(it|that|them|this)\s+(in|on|with)\s+(google|chrome)\b", low):
        urls.append("https://www.google.com")
        return list(dict.fromkeys(urls))

    # Search patterns (before bare site open)
    search_patterns = [
        # search/find <q> on/in youtube|google|…
        r"(?:search|find|look\s*up|google)\s+(?:for\s+)?(.+?)\s+(?:on|in|via|using)\s+(youtube|yt|google|wikipedia|wiki|leetcode|github|reddit|netflix)\b",
        # youtube/google search <q>
        r"\b(youtube|yt|google|wikipedia|wiki|leetcode|github|reddit|netflix)\s+(?:search|find)\s+(?:for\s+)?(.+)$",
        # search youtube for <q> / search on youtube <q>
        r"(?:search|find)\s+(?:on\s+)?(youtube|yt|google|wikipedia|wiki|leetcode|github|reddit|netflix)\s+(?:for\s+)?(.+)$",
        # google <query>  (no "open")
        r"^\s*google\s+(.+)$",
        # wikipedia <topic>
        r"^\s*(?:wikipedia|wiki)\s+(.+)$",
    ]
    search_engines_hit: set[str] = set()
    for pat in search_patterns:
        m = re.search(pat, low, flags=re.I)
        if not m:
            continue
        groups = [g for g in m.groups() if g]
        if len(groups) == 1 and pat.startswith(r"^\s*google"):
            urls.append(_search_url("google", groups[0]))
            search_engines_hit.add("google")
            continue
        if len(groups) == 1 and "wikipedia" in pat:
            urls.append(_search_url("wikipedia", groups[0]))
            search_engines_hit.add("wikipedia")
            continue
        if len(groups) >= 2:
            a, b = groups[0].strip(), groups[1].strip()
            engines = {
                "youtube", "yt", "google", "wikipedia", "wiki",
                "leetcode", "github", "reddit", "netflix",
            }
            if a.lower() in engines:
                eng = {"yt": "youtube", "wiki": "wikipedia"}.get(a.lower(), a.lower())
                urls.append(_search_url(a, b))
                search_engines_hit.add(eng)
            elif b.lower() in engines:
                eng = {"yt": "youtube", "wiki": "wikipedia"}.get(b.lower(), b.lower())
                urls.append(_search_url(b, a))
                search_engines_hit.add(eng)
            else:
                urls.append(_search_url("google", f"{a} {b}"))
                search_engines_hit.add("google")

    # Strip browser-choice noise: "in chrome", "on google chrome"
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
        )
    ) or cleaned.startswith("open") or cleaned.startswith("launch")

    # Collect site homes in mention order (not alphabetically)
    if wants_open or urls:
        mentioned: list[tuple[int, str, str]] = []  # (pos, name, url)
        for name in SITE_HOME.keys():
            m = re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", cleaned)
            if not m:
                continue
            canon = name
            if name in {"yt"}:
                canon = "youtube"
            if name in {"wiki"}:
                canon = "wikipedia"
            # Skip home page if we already built a search URL for this engine
            if canon in search_engines_hit or name in search_engines_hit:
                continue
            # "in google" as browser choice, not open google.com
            if name == "google" and re.search(r"\b(in|on|with|using)\s+google\b", low) and not re.search(
                r"\b(open|go to|visit)\s+google\b", low
            ):
                continue
            mentioned.append((m.start(), name, SITE_HOME[name]))
        mentioned.sort(key=lambda t: t[0])
        for _, _, home in mentioned:
            if home not in urls:
                urls.append(home)

    # Bare domain tokens
    if wants_open:
        for m in re.finditer(r"\b([a-z0-9-]+\.(?:com|org|net|io|dev|ai))\b", cleaned):
            u = "https://" + m.group(1)
            if u not in urls:
                urls.append(u)

    # Dedupe preserving order
    out: list[str] = []
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
        # exclude pure coding asks that happen to mention a site
        if re.search(
            r"\b(solve|implement|write\s+code|fix\s+bug|refactor)\b", low
        ) and not re.search(r"\b(open|search|google|browse|go to|visit)\b", low):
            return False
        return True
    if re.search(
        r"\b(search|google|browse|youtube|wikipedia)\b", low
    ) and re.search(r"\b(for|on|about)\b", low):
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
    return f"Opened in your {note}: {', '.join(opened)}."
