"""
LeetCode Tool — Fast, direct access to today's LeetCode Daily Challenge
using LeetCode's public GraphQL API. No browser scraping required.
"""

import httpx
import json
import re


LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"

DAILY_CHALLENGE_QUERY = """
query questionOfToday {
  activeDailyCodingChallengeQuestion {
    date
    link
    question {
      questionId
      questionFrontendId
      title
      titleSlug
      difficulty
      content
      exampleTestcases
      topicTags {
        name
      }
      hints
      sampleTestCase
      codeSnippets {
        lang
        langSlug
        code
      }
    }
  }
}
"""


async def get_daily_challenge() -> dict:
    """
    Fetches today's LeetCode Daily Challenge directly from the GraphQL API.
    Returns a dict with title, id, difficulty, url, description, examples, tags, hints.
    """
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    payload = {"query": DAILY_CHALLENGE_QUERY, "operationName": "questionOfToday"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(LEETCODE_GRAPHQL_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    challenge = data["data"]["activeDailyCodingChallengeQuestion"]
    q = challenge["question"]

    # Strip HTML tags from content
    raw_html = q.get("content", "")
    clean_text = re.sub(r"<[^>]+>", "", raw_html)
    clean_text = re.sub(r"&lt;", "<", clean_text)
    clean_text = re.sub(r"&gt;", ">", clean_text)
    clean_text = re.sub(r"&amp;", "&", clean_text)
    clean_text = re.sub(r"&nbsp;", " ", clean_text)
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()

    tags = [t["name"] for t in q.get("topicTags", [])]

    python3_snippet = ""
    for snippet in q.get("codeSnippets", []):
        if snippet["langSlug"] == "python3":
            python3_snippet = snippet["code"]
            break

    return {
        "title": q["title"],
        "id": q["questionFrontendId"],
        "difficulty": q["difficulty"],
        "url": f"https://leetcode.com{challenge['link']}",
        "date": challenge["date"],
        "description": clean_text,
        "examples": q.get("exampleTestcases", ""),
        "tags": tags,
        "hints": q.get("hints", []),
        "snippet": python3_snippet,
    }


def format_problem_for_llm(problem: dict) -> str:
    """Format a LeetCode problem dict into a clean prompt for Qwen."""
    lines = [
        f"LeetCode #{problem['id']} — {problem['title']}",
        f"Difficulty: {problem['difficulty']}",
        f"URL: {problem['url']}",
        f"Topics: {', '.join(problem['tags'])}",
        "",
        "Problem Description:",
        problem["description"],
    ]
    if problem.get("examples"):
        lines += ["", "Example Test Cases:", problem["examples"]]
    if problem.get("hints"):
        lines += ["", "Hints:"]
        for i, h in enumerate(problem["hints"], 1):
            lines.append(f"  {i}. {h}")
    if problem.get("snippet"):
        lines += ["", "Starter Code (MUST USE THIS CLASS SIGNATURE):", problem["snippet"]]
    return "\n".join(lines)


def parse_problem_sections(problem: dict) -> dict:
    """Extract structured sections from a LeetCode problem dict."""
    description = problem.get("description", "")
    examples = problem.get("examples", "")

    constraints = ""
    constraint_match = re.search(
        r"Constraints?:?\s*(.+?)(?:\n\n|$)", description, re.DOTALL | re.IGNORECASE
    )
    if constraint_match:
        constraints = constraint_match.group(1).strip()

    return {
        "title": problem.get("title", ""),
        "statement": description,
        "examples": examples,
        "constraints": constraints,
        "difficulty": problem.get("difficulty", ""),
        "id": problem.get("id", ""),
    }


async def fetch_solution_from_web(problem: dict) -> str | None:
    """
    Fetches a working Python solution for a LeetCode problem from the web.

    Strategy:
      1. Try walkccc.me (clean Python solutions, no auth)
      2. Try GitHub search via DuckDuckGo
      3. Scrape page, extract class Solution code block

    Returns the Python code string on success, or None on failure.
    """
    problem_id = problem["id"]
    title_slug = re.sub(r"[^a-z0-9-]", "-", problem["title"].lower()).strip("-")
    title_slug = re.sub(r"-+", "-", title_slug)

    # Candidate solution URLs to try in order
    candidate_urls = [
        # walkccc.me — clean, minimal Python solutions
        f"https://walkccc.me/LeetCode/problems/{problem_id}/",
        f"https://raw.githubusercontent.com/walkccc/LeetCode/main/Python/{problem_id:0>4}.py",
        f"https://raw.githubusercontent.com/walkccc/LeetCode/main/Python3/{problem_id:0>4}.py",
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,text/plain,*/*",
    }

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        # Try direct known URLs first
        for url in candidate_urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    text = resp.text
                    code = _extract_python_code(text)
                    if code:
                        return code
            except Exception:
                continue

        # Fallback: DuckDuckGo search for a GitHub solution
        search_queries = [
            f"leetcode {problem_id} {problem['title'].replace(' ', '+')} python solution site:github.com",
            f"leetcode {problem_id} python \"class Solution\" site:github.com",
        ]
        for query in search_queries:
            try:
                encoded = re.sub(r"\s+", "+", query)
                resp = await client.get(
                    f"https://html.duckduckgo.com/html/?q={encoded}",
                    headers={**headers, "Accept": "text/html"},
                    timeout=8,
                )
                if resp.status_code != 200:
                    continue

                # Extract raw GitHub URLs from DDG results
                raw_urls = re.findall(
                    r"uddg=([^&\"]+)",
                    resp.text,
                )
                for raw in raw_urls[:6]:
                    try:
                        from urllib.parse import unquote
                        url = unquote(raw)
                    except Exception:
                        url = raw

                    # Convert GitHub blob URL → raw URL
                    if "github.com" in url and "/blob/" in url:
                        url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")

                    if "raw.githubusercontent.com" in url and url.endswith(".py"):
                        try:
                            r2 = await client.get(url, headers=headers, timeout=6)
                            if r2.status_code == 200:
                                code = _extract_python_code(r2.text)
                                if code:
                                    return code
                        except Exception:
                            continue
            except Exception:
                continue

    return None


def _extract_python_code(text: str) -> str | None:
    """
    Extracts a valid Python class Solution block from text (web scraping or LLM output).
    """
    # 1. Try to extract from markdown code fences (```python ... ```)
    for fence_match in re.finditer(r"```(?:python)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE):
        snippet = fence_match.group(1).strip()
        if "class Solution" in snippet:
            return snippet

    # 2. Try to extract from HTML <code> blocks
    for code_match in re.finditer(r"<code[^>]*>(.*?)</code>", text, re.DOTALL | re.IGNORECASE):
        snippet = re.sub(r"<[^>]+>", "", code_match.group(1)).strip()
        if "class Solution" in snippet and "def " in snippet:
            return snippet

    # 3. Fallback: raw text parsing (find start of code and end before trailing prose)
    if "class Solution" in text:
        lines = text.splitlines()
        start = -1
        # Find first import or class Solution
        for i, line in enumerate(lines):
            if line.startswith("from ") or line.startswith("import ") or line.startswith("class Solution"):
                start = i
                break
        if start != -1:
            # We found the start. Now find where the code ends (e.g. next markdown header or end of file)
            end = len(lines)
            # Find the first line after start that looks like markdown prose, but be careful not to break on comments
            for i in range(start + 1, len(lines)):
                stripped = lines[i].strip()
                if stripped.startswith("```") or (stripped and not stripped.startswith("#") and not stripped.startswith(" ") and not stripped.startswith("def") and not stripped.startswith("class") and not stripped.startswith("@") and not stripped.startswith("return") and not stripped.startswith("if") and not stripped.startswith("for") and not stripped.startswith("while") and not stripped.startswith("elif") and not stripped.startswith("else") and not stripped.startswith("try") and not stripped.startswith("except") and not stripped.startswith("pass") and not stripped.startswith("continue") and not stripped.startswith("break") and not stripped.startswith("import") and not stripped.startswith("from") and not stripped.startswith("print") and not stripped.startswith("yield") and not stripped.startswith("assert") and not stripped.startswith("with") and not stripped.startswith("raise") and not stripped.startswith("global") and not stripped.startswith("nonlocal") and not stripped.startswith("del") and not stripped.startswith("def") and "=" not in stripped and "(" not in stripped):
                    # Looks like trailing prose (e.g. "To solve this...", "### Explanation")
                    # We look for lines that are clearly English prose
                    if re.match(r"^[A-Z][a-z]+|^###|^\*\*", stripped):
                        end = i
                        break
            
            # Additional safety: trim blank lines from end
            while end > start and not lines[end-1].strip():
                end -= 1
            return "\n".join(lines[start:end]).strip()

    return None
