"""Accessibility Tree Normalization and Filtering for Computer Use.

Prunes raw accessibility trees down to actionable, compact token-budgeted
representations suitable for LLM reasoning.
"""

from __future__ import annotations

from typing import Any

from core.computer.contracts import ElementInfo

INTERACTABLE_ROLES = frozenset({
    "button",
    "edit",
    "textbox",
    "combobox",
    "checkbox",
    "radiobutton",
    "menuitem",
    "tabitem",
    "treeitem",
    "listitem",
    "hyperlink",
    "slider",
    "splitbutton",
})

CONTAINER_ROLES = frozenset({
    "window",
    "pane",
    "group",
    "tab",
    "tree",
    "list",
    "menubar",
    "menu",
    "toolbar",
    "document",
    "custom",
})


class AccessibilityTreeFilter:
    """Filters and normalizes an accessibility tree into a compact observation."""

    def __init__(self, max_controls: int = 50, max_depth: int = 4):
        self.max_controls = max_controls
        self.max_depth = max_depth

    def filter_tree(self, root: ElementInfo | None) -> dict[str, Any]:
        """Convert an ElementInfo tree to a filtered, concise dictionary."""
        if root is None:
            return {"controls": []}

        collected: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()

        def _walk(node: ElementInfo, depth: int) -> None:
            if len(collected) >= self.max_controls:
                return

            role = (node.role or "").lower()
            name = (node.name or "").strip()
            auto_id = (node.automation_id or "").strip()

            # Ignore zero-dimension bounding boxes
            left, top, right, bottom = node.bounds
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                # Still check children
                for child in node.children:
                    _walk(child, depth + 1)
                return

            # Deduplication key
            key = (role, name, auto_id)

            # Keep if interactable or has a meaningful name (skip root window container at depth 0)
            is_actionable = (role in INTERACTABLE_ROLES or (name and role in CONTAINER_ROLES)) and not (depth == 0 and role in {"window", "dialog"})
            if is_actionable and key not in seen_keys:
                seen_keys.add(key)
                item: dict[str, Any] = {
                    "id": node.id,
                    "role": node.role,
                    "name": name,
                    "enabled": node.is_enabled,
                }
                if node.value is not None:
                    item["value"] = node.value[:100]
                if node.automation_id:
                    item["automation_id"] = node.automation_id
                if node.is_focused:
                    item["focused"] = True
                if node.supported_patterns:
                    item["supported_patterns"] = list(node.supported_patterns)
                item["bounds"] = [left, top, right, bottom]
                collected.append(item)

            if depth < self.max_depth:
                for child in node.children:
                    _walk(child, depth + 1)

        _walk(root, 0)
        return {
            "root_role": root.role,
            "root_name": root.name,
            "controls": collected,
            "total_controls_discovered": len(collected),
        }

    def find_element(
        self,
        root: ElementInfo | None,
        *,
        name: str = "",
        role: str = "",
        automation_id: str = "",
    ) -> ElementInfo | None:
        """Find an element in the tree matching role, name, or automation ID."""
        if root is None:
            return None

        target_name = (name or "").strip().lower()
        target_role = (role or "").strip().lower()
        target_id = (automation_id or "").strip()

        candidates: list[tuple[int, ElementInfo]] = []

        def _search(node: ElementInfo) -> None:
            n_name = (node.name or "").lower()
            n_role = (node.role or "").lower()
            n_id = node.automation_id or ""

            # If criteria are specified, they must match
            if target_id and target_id != n_id:
                pass
            elif target_name and (target_name != n_name and target_name not in n_name):
                pass
            elif target_role and target_role != n_role:
                pass
            else:
                score = 0
                if target_id and target_id == n_id:
                    score += 10
                if target_name:
                    if target_name == n_name:
                        score += 5
                    elif target_name in n_name:
                        score += 2
                if target_role and target_role == n_role:
                    score += 3
                if score > 0:
                    candidates.append((score, node))

            for child in node.children:
                _search(child)

        _search(root)
        if candidates:
            # Sort by best score descending
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

        return None

    def is_stale(self, element: ElementInfo | None, current_root: ElementInfo | None) -> bool:
        """Verify if an element reference is still valid within the current tree."""
        if element is None or current_root is None:
            return True
        if element.generation_id != current_root.generation_id:
            # Check if element exists identically in the current tree
            current_match = self.find_element(
                current_root,
                name=element.name,
                role=element.role,
                automation_id=element.automation_id or "",
            )
            if not current_match:
                return True
            # If bounds completely moved or changed, mark stale
            if current_match.bounds != element.bounds:
                return True
        return False
