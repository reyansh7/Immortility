"""Capability router — picks a model for a role under the VRAM budget.

Selection is role-aware: the brain orchestrates even when a coding specialist is
installed, and a coding request prefers the specialist but degrades to the brain
rather than failing. Vision never degrades to a text model — an unavailable
capability is reported as unavailable.
"""

from __future__ import annotations

import logging

from models.registry import ModelRegistry, get_registry
from models.types import (
    CAP_ASR,
    CAP_CHAT,
    CAP_CODE,
    CAP_EMBED,
    CAP_RERANK,
    CAP_TTS,
    CAP_VISION,
    ModelSpec,
    Selection,
)
from models.vram import probe_gpu

logger = logging.getLogger(__name__)

ROLE_BRAIN = "brain"
ROLE_CODE = "code"
ROLE_VISION = "vision"
ROLE_EMBED = "embed"
ROLE_RERANK = "rerank"
ROLE_ASR = "asr"
ROLE_TTS = "tts"

ROLE_CAPABILITY: dict[str, str] = {
    ROLE_BRAIN: CAP_CHAT,
    "chat": CAP_CHAT,
    ROLE_CODE: CAP_CODE,
    ROLE_VISION: CAP_VISION,
    ROLE_EMBED: CAP_EMBED,
    ROLE_RERANK: CAP_RERANK,
    ROLE_ASR: CAP_ASR,
    ROLE_TTS: CAP_TTS,
}


def _vram_budget_mb(spec: ModelSpec, registry: ModelRegistry) -> float:
    """VRAM a spec may claim.

    Heavy models are admitted against *total* VRAM because the manager evicts
    the resident heavy model before switching. Light models are admitted against
    what is free right now.
    """
    snapshot = probe_gpu()
    total = snapshot.vram_total_mb or float(registry.defaults.total_vram_mb)
    if spec.heavy:
        return total
    free = snapshot.vram_free_mb
    return free if free is not None else total


def _skip_reason(
    spec: ModelSpec, registry: ModelRegistry, provider: str | None = None
) -> str | None:
    """Why this spec cannot serve, or None when it can."""
    if registry.is_disabled(spec):
        return f"disabled via {spec.enabled_env}"
    if not registry.serves_provider(spec, provider):
        return f"runtime '{spec.runtime}' does not match the active provider"
    if not spec.configured:
        hint = f" (set {spec.env_model_var})" if spec.env_model_var else ""
        return f"not configured{hint}"
    if spec.min_free_vram_mb > 0:
        budget = _vram_budget_mb(spec, registry)
        if spec.min_free_vram_mb > budget:
            return (
                f"needs {spec.min_free_vram_mb} MB VRAM, budget is {int(budget)} MB"
            )
    return None


def _candidates(role: str, capability: str, registry: ModelRegistry) -> list[ModelSpec]:
    primary = list(registry.for_role(role))
    primary_ids = {s.id for s in primary}
    others: list[ModelSpec] = []
    for spec in registry.for_capability(capability):
        if spec.id in primary_ids:
            continue
        # A vision model is never a silent stand-in for text work.
        if spec.role == ROLE_VISION and capability != CAP_VISION:
            continue
        others.append(spec)
    return primary + others


def select_for_role(
    role: str,
    *,
    capability: str | None = None,
    registry: ModelRegistry | None = None,
    provider: str | None = None,
) -> Selection | None:
    """Best model for ``role``, or None when nothing can serve it."""
    reg = registry or get_registry()
    cap = capability or ROLE_CAPABILITY.get(role, CAP_CHAT)
    candidates = _candidates(role, cap, reg)
    if not candidates:
        return None

    skipped: list[str] = []
    for spec in candidates:
        reason = _skip_reason(spec, reg, provider)
        if reason:
            skipped.append(f"{spec.id}: {reason}")
            continue
        # "degraded" means another role is standing in (e.g. the brain doing
        # code work), not merely that an uninstalled entry sorted ahead.
        degraded = spec.role != role
        why = f"role={role} capability={cap}"
        if degraded:
            why += " (substitute)"
        return Selection(
            spec=spec,
            capability=cap,
            reason=why,
            degraded=degraded,
            skipped=tuple(skipped),
        )

    logger.debug("No model available for role=%s: %s", role, "; ".join(skipped))
    return None


def select_for_capability(
    capability: str,
    *,
    registry: ModelRegistry | None = None,
    provider: str | None = None,
) -> Selection | None:
    """Best model declaring ``capability`` regardless of role."""
    reg = registry or get_registry()
    for role, cap in ROLE_CAPABILITY.items():
        if cap == capability and reg.for_role(role):
            return select_for_role(
                role, capability=capability, registry=reg, provider=provider
            )
    return select_for_role(
        capability, capability=capability, registry=reg, provider=provider
    )


def model_id_for_role(
    role: str,
    *,
    registry: ModelRegistry | None = None,
    provider: str | None = None,
) -> str:
    """Concrete model id for ``role``, or "" when unavailable."""
    selection = select_for_role(role, registry=registry, provider=provider)
    return selection.spec.model_id if selection else ""


def capability_available(
    capability: str,
    *,
    registry: ModelRegistry | None = None,
    provider: str | None = None,
) -> bool:
    return select_for_capability(capability, registry=registry, provider=provider) is not None


def fallback_model_id(
    role: str = ROLE_BRAIN,
    *,
    primary: str = "",
    registry: ModelRegistry | None = None,
    provider: str | None = None,
) -> str:
    """Next model id for ``role`` after ``primary`` (empty when there is none)."""
    reg = registry or get_registry()
    cap = ROLE_CAPABILITY.get(role, CAP_CHAT)
    current = (primary or "").strip()
    for spec in _candidates(role, cap, reg):
        if _skip_reason(spec, reg, provider):
            continue
        if spec.model_id and spec.model_id != current:
            return spec.model_id
    return ""


def describe_roles(registry: ModelRegistry | None = None) -> dict[str, str]:
    """Role → chosen model id (or a short reason), for logs and the doctor."""
    reg = registry or get_registry()
    out: dict[str, str] = {}
    for role in (ROLE_BRAIN, ROLE_CODE, ROLE_VISION, ROLE_EMBED, ROLE_RERANK, ROLE_ASR, ROLE_TTS):
        if not reg.for_role(role):
            continue
        selection = select_for_role(role, registry=reg)
        if selection is None:
            specs = reg.for_role(role)
            disabled = [s for s in specs if reg.is_disabled(s)]
            if disabled and len(disabled) == len(specs):
                out[role] = f"disabled ({disabled[0].enabled_env})"
            else:
                out[role] = "unavailable"
            continue
        label = selection.spec.model_id
        if selection.degraded:
            label += f" (via {selection.spec.id})"
        out[role] = label
    return out
