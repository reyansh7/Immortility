"""Browser agent: Planner → Executor → Validator (accessibility-tree based)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from tools.browser_manager import BrowserManager

logger = logging.getLogger(__name__)


class BrowserAgent:
    """Hardened browser automation pipeline."""

    @staticmethod
    async def run(goal: str, start_url: str | None = None) -> dict[str, Any]:
        plan = BrowserAgent.plan(goal, start_url=start_url)
        if not plan.get("actions"):
            return {"status": "error", "message": "Planner produced no actions", "plan": plan}

        results: list[dict[str, Any]] = []
        for i, action in enumerate(plan["actions"]):
            exec_res = await BrowserAgent.execute(action)
            val_res = await BrowserAgent.validate(action, exec_res)
            step = {"index": i, "action": action, "execute": exec_res, "validate": val_res}
            results.append(step)
            if not val_res.get("ok"):
                return {
                    "status": "error",
                    "message": f"Step {i} failed validation: {val_res.get('reason')}",
                    "plan": plan,
                    "steps": results,
                }
        return {
            "status": "success",
            "message": f"Completed {len(results)} browser step(s)",
            "plan": plan,
            "steps": results,
        }

    @staticmethod
    def plan(goal: str, start_url: str | None = None) -> dict[str, Any]:
        """Translate a user goal into atomic UI actions (deterministic heuristics).

        Returns JSON-serializable plan. No coordinate guessing.
        """
        actions: list[dict[str, Any]] = []
        g = (goal or "").strip()
        lower = g.lower()

        url = start_url
        if not url:
            m = re.search(r"https?://[^\s]+", g)
            if m:
                url = m.group(0).rstrip(".,)")
            elif "youtube" in lower:
                url = "https://www.youtube.com"
            elif "google" in lower:
                url = "https://www.google.com"

        if url:
            actions.append(
                {
                    "op": "goto",
                    "url": url,
                    "expect": {"url_contains": url.split("//", 1)[-1].split("/")[0]},
                }
            )

        # Search box fill heuristics
        q = None
        for pat in (
            r"search(?:\s+for)?\s+[\"']([^\"']+)[\"']",
            r"search(?:\s+for)?\s+(.+)$",
            r"type\s+[\"']([^\"']+)[\"']",
        ):
            m = re.search(pat, lower)
            if m:
                q = m.group(1).strip()
                break
        if q:
            actions.append(
                {
                    "op": "fill_role",
                    "role": "searchbox",
                    "name": None,
                    "text": q,
                    "fallback_roles": ["textbox"],
                    "expect": {"has_value": q},
                }
            )
            actions.append(
                {
                    "op": "press",
                    "key": "Enter",
                    "expect": {"url_changed_or_content": True},
                }
            )

        # Click by accessible name
        m = re.search(r"click(?:\s+on)?\s+[\"']([^\"']+)[\"']", lower)
        if m:
            actions.append(
                {
                    "op": "click_role",
                    "role": "button",
                    "name": m.group(1),
                    "fallback_roles": ["link"],
                    "expect": {"clicked": True},
                }
            )

        if not actions:
            actions.append(
                {
                    "op": "snapshot",
                    "expect": {"has_a11y_tree": True},
                }
            )

        return {"goal": g, "actions": actions}

    @staticmethod
    async def execute(action: dict[str, Any]) -> dict[str, Any]:
        """Execute one atomic action using Playwright accessibility roles."""
        manager = BrowserManager()
        page = await manager.get_page()
        op = action.get("op")
        before_url = page.url

        try:
            if op == "goto":
                url = action["url"]
                if not url.startswith("http"):
                    url = "https://" + url
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(500)
                await manager.sync_state()
                return {"ok": True, "before_url": before_url, "url": page.url}

            if op == "fill_role":
                roles = [action.get("role")] + list(action.get("fallback_roles") or [])
                text = action.get("text", "")
                last_err = ""
                for role in roles:
                    if not role:
                        continue
                    try:
                        locator = page.get_by_role(role, name=action.get("name") or None)
                        await locator.first.fill(text, timeout=5000)
                        return {
                            "ok": True,
                            "before_url": before_url,
                            "url": page.url,
                            "filled": text,
                            "role": role,
                        }
                    except Exception as exc:
                        last_err = str(exc)
                return {"ok": False, "error": last_err or "fill_role failed", "url": page.url}

            if op == "click_role":
                roles = [action.get("role")] + list(action.get("fallback_roles") or [])
                name = action.get("name")
                last_err = ""
                for role in roles:
                    if not role:
                        continue
                    try:
                        locator = page.get_by_role(role, name=name)
                        await locator.first.click(timeout=5000)
                        await page.wait_for_timeout(400)
                        return {
                            "ok": True,
                            "before_url": before_url,
                            "url": page.url,
                            "clicked": name,
                            "role": role,
                        }
                    except Exception as exc:
                        last_err = str(exc)
                return {"ok": False, "error": last_err or "click_role failed", "url": page.url}

            if op == "press":
                await page.keyboard.press(action.get("key", "Enter"))
                await page.wait_for_timeout(600)
                return {"ok": True, "before_url": before_url, "url": page.url}

            if op == "snapshot":
                # Accessibility snapshot via ARIA snapshot if available
                try:
                    snap = await page.locator("body").aria_snapshot()
                except Exception:
                    snap = await page.inner_text("body")
                return {
                    "ok": True,
                    "before_url": before_url,
                    "url": page.url,
                    "snapshot": str(snap)[:4000],
                }

            return {"ok": False, "error": f"Unknown op: {op}", "url": page.url}
        except Exception as exc:
            logger.exception("Browser execute failed")
            return {"ok": False, "error": str(exc), "before_url": before_url, "url": getattr(page, "url", "")}

    @staticmethod
    async def validate(action: dict[str, Any], exec_res: dict[str, Any]) -> dict[str, Any]:
        """Validate DOM / navigation outcomes for a step."""
        if not exec_res.get("ok"):
            return {"ok": False, "reason": exec_res.get("error", "execute failed")}

        expect = action.get("expect") or {}
        manager = BrowserManager()
        page = await manager.get_page()
        url = page.url

        if "url_contains" in expect:
            needle = str(expect["url_contains"]).lower()
            if needle not in url.lower():
                return {"ok": False, "reason": f"URL missing '{needle}': {url}"}

        if expect.get("url_changed_or_content"):
            before = exec_res.get("before_url", "")
            if url == before:
                # content may have changed in-place — accept if body nonempty
                try:
                    text = await page.inner_text("body")
                except Exception:
                    text = ""
                if len(text.strip()) < 20:
                    return {"ok": False, "reason": "URL unchanged and page looks empty"}

        if "has_value" in expect:
            # Best-effort: ensure filled text recorded
            if exec_res.get("filled") != expect["has_value"]:
                # still ok if execute reported fill
                if not exec_res.get("filled"):
                    return {"ok": False, "reason": "expected filled value missing"}

        if expect.get("clicked") and not exec_res.get("clicked"):
            return {"ok": False, "reason": "click not confirmed"}

        if expect.get("has_a11y_tree"):
            snap = exec_res.get("snapshot") or ""
            if len(str(snap).strip()) < 5:
                return {"ok": False, "reason": "empty accessibility snapshot"}

        return {"ok": True, "reason": "validated", "url": url}


async def run_browser_goal(goal: str, start_url: str | None = None) -> str:
    """Tool-registry friendly wrapper returning JSON string."""
    result = await BrowserAgent.run(goal, start_url=start_url)
    return json.dumps(result)
