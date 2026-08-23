"""Immortility model layer — registry, capability router, hardware-aware manager.

Nothing in this package imports an inference library at module scope, so it is
cheap to import from the CLI, the HUD, and tests. Public entry points:

    from models.registry import get_registry
    from models.router import select_for_role, model_id_for_role
    from models.manager import get_manager
"""

from __future__ import annotations

__all__ = [
    "get_registry",
    "get_manager",
    "select_for_role",
    "model_id_for_role",
    "capability_available",
]


def get_registry():
    from models.registry import get_registry as _get

    return _get()


def get_manager():
    from models.manager import get_manager as _get

    return _get()


def select_for_role(role: str):
    from models.router import select_for_role as _select

    return _select(role)


def model_id_for_role(role: str) -> str:
    from models.router import model_id_for_role as _resolve

    return _resolve(role)


def capability_available(capability: str) -> bool:
    from models.router import capability_available as _available

    return _available(capability)
