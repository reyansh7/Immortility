"""Model registry — parses config/models.yaml and applies environment overrides.

Environment always wins over the YAML file so an operator can swap a model
without editing config. The registry never contacts a server and never loads
weights; it only answers "what is configured".
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from models.types import (
    KNOWN_CAPABILITIES,
    KNOWN_RUNTIMES,
    RUNTIME_AUTO,
    RUNTIME_OLLAMA,
    RUNTIME_OPENAI_COMPAT,
    SERVER_RUNTIMES,
    ModelConfigError,
    ModelSpec,
)

logger = logging.getLogger(__name__)

CONFIG_RELATIVE_PATH = Path("config") / "models.yaml"

# Chat-model env vars per provider. Kept strict: a model id is not portable
# between runtimes, so an Ollama tag must never satisfy a vLLM spec.
_PROVIDER_MODEL_VARS: dict[str, tuple[str, ...]] = {
    "ollama": ("OLLAMA_MODEL",),
    "vllm": ("VLLM_MODEL", "OPENAI_MODEL"),
    "openai": ("OPENAI_MODEL", "VLLM_MODEL"),
}

_MAX_SAFE_CONTEXT = 32768


@dataclass(frozen=True)
class RegistryDefaults:
    total_vram_mb: int = 8188
    heavy_threshold_mb: int = 3000
    context: int = 8192


def config_path() -> Path:
    from core.repo_paths import get_repo_root

    return get_repo_root() / CONFIG_RELATIVE_PATH


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def local_provider_name() -> str:
    """Active local runtime name (ollama | vllm | openai).

    Delegates to ``core.llm`` so provider selection has exactly one
    implementation. Imported lazily: ``core.llm`` asks this module for role
    resolution, and a module-level import here would be circular.
    """
    try:
        from core.llm import _local_provider_name

        return _local_provider_name()
    except Exception:  # pragma: no cover - defensive
        return "vllm"


def _env_chat_model(provider: str) -> str:
    for var in _PROVIDER_MODEL_VARS.get(provider, ("VLLM_MODEL", "OLLAMA_MODEL")):
        value = _env(var)
        if value and value.lower() not in {"auto", "none"}:
            return value
    return ""


def _provider_for_runtime(runtime: str) -> str:
    """Which env vars an "auto" model_id override should read."""
    if runtime == RUNTIME_OLLAMA:
        return "ollama"
    if runtime == RUNTIME_OPENAI_COMPAT:
        active = local_provider_name()
        return active if active in {"vllm", "openai"} else "vllm"
    return local_provider_name()


def _resolve_context(raw_context: int) -> int:
    override = _env("IMMORTILITY_NUM_CTX")
    if not override:
        return raw_context
    try:
        wanted = int(override)
    except ValueError:
        logger.warning("IMMORTILITY_NUM_CTX=%r is not an integer — ignoring", override)
        return raw_context
    if wanted <= 0:
        return raw_context
    if wanted > _MAX_SAFE_CONTEXT:
        logger.warning(
            "IMMORTILITY_NUM_CTX=%d exceeds the tested ceiling %d — clamping",
            wanted,
            _MAX_SAFE_CONTEXT,
        )
        wanted = _MAX_SAFE_CONTEXT
    return wanted


class ModelRegistry:
    """Immutable view over the configured fleet."""

    def __init__(self, specs: list[ModelSpec], defaults: RegistryDefaults) -> None:
        self._specs = tuple(specs)
        self._by_id = {spec.id: spec for spec in self._specs}
        self.defaults = defaults

    # ── Lookups ─────────────────────────────────────────────────────────

    def all(self) -> tuple[ModelSpec, ...]:
        return self._specs

    def get(self, spec_id: str) -> ModelSpec | None:
        return self._by_id.get(spec_id)

    def for_role(self, role: str) -> tuple[ModelSpec, ...]:
        return tuple(
            sorted(
                (s for s in self._specs if s.role == role),
                key=lambda s: (s.priority, s.id),
            )
        )

    def for_capability(self, capability: str) -> tuple[ModelSpec, ...]:
        """Specs that declare ``capability``, best (lowest priority) first."""
        return tuple(
            sorted(
                (s for s in self._specs if s.supports(capability)),
                key=lambda s: (s.priority, s.id),
            )
        )

    def is_disabled(self, spec: ModelSpec) -> bool:
        """True when a spec is switched off by its ``enabled_env`` flag.

        Evaluated live so toggling an env var takes effect without a reload.
        """
        if not spec.enabled_env:
            return False
        return not _truthy(os.environ.get(spec.enabled_env))

    def effective_runtime(self, spec: ModelSpec) -> str:
        """Resolve ``runtime: auto`` against the active local provider."""
        if spec.runtime != RUNTIME_AUTO:
            return spec.runtime
        return RUNTIME_OLLAMA if local_provider_name() == "ollama" else RUNTIME_OPENAI_COMPAT

    def serves_provider(self, spec: ModelSpec, provider: str | None = None) -> bool:
        """True when ``spec`` can be served by the active local provider.

        ``runtime: auto`` follows whatever provider is active. A spec pinned to
        ``ollama`` is not offered to a vLLM endpoint, and vice versa, because the
        model ids are not interchangeable between runtimes.
        """
        if spec.runtime not in SERVER_RUNTIMES:
            return True
        if spec.runtime == RUNTIME_AUTO:
            return True
        active = (provider or local_provider_name()).lower()
        if spec.runtime == RUNTIME_OLLAMA:
            return active == "ollama"
        return active in {"vllm", "openai", "openai_compat", "local"}


# ── Loading ─────────────────────────────────────────────────────────────


def _spec_from_raw(raw: Any, defaults: RegistryDefaults, seen: set[str]) -> ModelSpec:
    if not isinstance(raw, dict):
        raise ModelConfigError(f"model entry must be a mapping, got {type(raw).__name__}")

    spec_id = str(raw.get("id") or "").strip()
    if not spec_id:
        raise ModelConfigError("model entry is missing 'id'")
    if spec_id in seen:
        raise ModelConfigError(f"duplicate model id: {spec_id}")
    seen.add(spec_id)

    role = str(raw.get("role") or "").strip()
    if not role:
        raise ModelConfigError(f"model '{spec_id}' is missing 'role'")

    runtime = str(raw.get("runtime") or RUNTIME_AUTO).strip()
    if runtime not in KNOWN_RUNTIMES:
        raise ModelConfigError(
            f"model '{spec_id}' has unknown runtime '{runtime}' "
            f"(expected one of {sorted(KNOWN_RUNTIMES)})"
        )

    raw_caps = raw.get("capabilities") or []
    if not isinstance(raw_caps, list) or not raw_caps:
        raise ModelConfigError(f"model '{spec_id}' needs a non-empty 'capabilities' list")
    caps = {str(c).strip() for c in raw_caps}
    unknown = caps - KNOWN_CAPABILITIES
    if unknown:
        raise ModelConfigError(
            f"model '{spec_id}' declares unknown capabilities {sorted(unknown)}"
        )

    def _int(key: str, fallback: int = 0) -> int:
        value = raw.get(key, fallback)
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ModelConfigError(
                f"model '{spec_id}' field '{key}' must be an integer, got {value!r}"
            ) from None

    vram_mb = _int("vram_mb")
    heavy = bool(raw.get("heavy", vram_mb >= defaults.heavy_threshold_mb))
    min_free = _int("min_free_vram_mb")
    if heavy and min_free <= 0:
        min_free = vram_mb

    env_model_var = str(raw.get("env_model_var") or "").strip()
    model_id = str(raw.get("model_id") or "").strip()

    # Environment override. "auto" means "whatever the provider's chat env var says".
    if env_model_var == "auto":
        override = _env_chat_model(_provider_for_runtime(runtime))
    elif env_model_var:
        override = _env(env_model_var)
    else:
        override = ""
    if override:
        model_id = override

    context = _int("context", defaults.context) or defaults.context
    if runtime in {RUNTIME_AUTO, RUNTIME_OLLAMA, RUNTIME_OPENAI_COMPAT}:
        context = _resolve_context(context)

    return ModelSpec(
        id=spec_id,
        role=role,
        runtime=runtime,
        model_id=model_id,
        capabilities=frozenset(caps),
        priority=_int("priority", 100),
        context=context,
        vram_mb=vram_mb,
        min_free_vram_mb=min_free,
        heavy=heavy,
        optional=bool(raw.get("optional", False)),
        device=str(raw.get("device") or "").strip(),
        dimension=_int("dimension"),
        env_model_var=env_model_var,
        enabled_env=str(raw.get("enabled_env") or "").strip(),
        notes=str(raw.get("notes") or "").strip(),
    )


def load_registry(path: Path | str | None = None) -> ModelRegistry:
    """Parse the fleet config. Raises ``ModelConfigError`` on bad input."""
    target = Path(path) if path else config_path()
    if not target.is_file():
        raise ModelConfigError(f"model config not found: {target}")

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency declared in requirements
        raise ModelConfigError("PyYAML is required to read config/models.yaml") from exc

    try:
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelConfigError(f"{target} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ModelConfigError(f"{target} must contain a top-level mapping")

    version = data.get("version", 1)
    if str(version) != "1":
        logger.warning("%s declares version %s; expected 1", target, version)

    raw_defaults = data.get("defaults") or {}
    if not isinstance(raw_defaults, dict):
        raise ModelConfigError("'defaults' must be a mapping")

    def _default_int(key: str, fallback: int) -> int:
        try:
            return int(raw_defaults.get(key, fallback))
        except (TypeError, ValueError):
            raise ModelConfigError(f"defaults.{key} must be an integer") from None

    defaults = RegistryDefaults(
        total_vram_mb=_default_int("total_vram_mb", 8188),
        heavy_threshold_mb=_default_int("heavy_threshold_mb", 3000),
        context=_default_int("context", 8192),
    )

    raw_models = data.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise ModelConfigError(f"{target} must define a non-empty 'models' list")

    seen: set[str] = set()
    specs = [_spec_from_raw(raw, defaults, seen) for raw in raw_models]
    return ModelRegistry(specs, defaults)


_registry: ModelRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ModelRegistry:
    """Process-wide registry singleton."""
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = load_registry()
            logger.debug("Model registry loaded with %d specs", len(_registry.all()))
        return _registry


def reset_registry_cache() -> None:
    """Force the next ``get_registry()`` to re-read config and env."""
    global _registry
    with _registry_lock:
        _registry = None
