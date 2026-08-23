"""Model manager — health probes and one-heavy-model-at-a-time admission.

The manager does not load weights itself; the inference server does that on the
first request. Its job is to (a) probe reachability without side effects, and
(b) evict the resident heavy model before a different heavy role is used, so an
8 GB card is never asked to hold two large models at once.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib.util import find_spec

from models.registry import ModelRegistry, get_registry
from models.router import ROLE_BRAIN, select_for_role
from models.types import (
    RUNTIME_FASTER_WHISPER,
    RUNTIME_OLLAMA,
    RUNTIME_OPENAI_COMPAT,
    RUNTIME_SAPI,
    RUNTIME_SENTENCE_TRANSFORMERS,
    ModelHealth,
    ModelSpec,
    Selection,
)

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class LoadResult:
    """Outcome of preparing a role for use."""

    selection: Selection | None
    health: ModelHealth | None = None
    evicted: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.selection is not None and (self.health is None or self.health.ok)

    @property
    def model_id(self) -> str:
        return self.selection.spec.model_id if self.selection else ""


def _http_json(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        body = response.read().decode("utf-8", errors="replace")
    if not body.strip():
        return {}
    return json.loads(body)


def _ollama_root() -> str:
    from core.llm import _ollama_api_root

    return _ollama_api_root()


def _openai_base(runtime_provider: str) -> str:
    from core.llm import local_base_url

    return local_base_url(runtime_provider)


def _tag_matches(tag: str, available: list[str]) -> bool:
    if tag in available:
        return True
    if f"{tag}:latest" in available:
        return True
    bare = tag.split(":", 1)[0]
    return any(name.split(":", 1)[0] == bare for name in available)


class ModelManager:
    """Process-wide model admission control."""

    _instance: "ModelManager | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self._registry = registry
        self._lock = threading.Lock()
        self._resident_heavy: ModelSpec | None = None

    @classmethod
    def get(cls) -> "ModelManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = ModelManager()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the singleton — used by tests."""
        with cls._instance_lock:
            cls._instance = None

    @property
    def registry(self) -> ModelRegistry:
        return self._registry or get_registry()

    def resident_heavy(self) -> ModelSpec | None:
        with self._lock:
            return self._resident_heavy

    # ── Health ──────────────────────────────────────────────────────────

    def health(self, spec: ModelSpec) -> ModelHealth:
        """Non-destructive reachability probe. Never raises."""
        registry = self.registry
        if registry.is_disabled(spec):
            return ModelHealth(spec.id, "disabled", f"{spec.enabled_env} is off")
        if not spec.configured:
            hint = f"set {spec.env_model_var}" if spec.env_model_var else "no model_id"
            return ModelHealth(spec.id, "unconfigured", hint)
        if not registry.serves_provider(spec):
            from models.registry import local_provider_name

            return ModelHealth(
                spec.id,
                "inactive",
                f"runtime {spec.runtime}, active provider is {local_provider_name()}",
            )

        runtime = registry.effective_runtime(spec)
        try:
            if runtime == RUNTIME_OLLAMA:
                return self._health_ollama(spec)
            if runtime == RUNTIME_OPENAI_COMPAT:
                return self._health_openai(spec)
            if runtime == RUNTIME_SENTENCE_TRANSFORMERS:
                return self._health_import(spec, "sentence_transformers")
            if runtime == RUNTIME_FASTER_WHISPER:
                return self._health_import(spec, "faster_whisper")
            if runtime == RUNTIME_SAPI:
                return self._health_import(spec, "pyttsx3")
        except Exception as exc:  # pragma: no cover - defensive
            return ModelHealth(spec.id, "error", str(exc))
        return ModelHealth(spec.id, "unknown", f"no probe for runtime {runtime}")

    def _health_ollama(self, spec: ModelSpec) -> ModelHealth:
        root = _ollama_root()
        try:
            body = _http_json(f"{root}/api/tags")
        except (urllib.error.URLError, OSError) as exc:
            return ModelHealth(spec.id, "unavailable", f"Ollama unreachable at {root} ({exc})")
        names = [str(m.get("model") or m.get("name") or "") for m in body.get("models") or []]
        if _tag_matches(spec.model_id, names):
            return ModelHealth(spec.id, "ok", f"{spec.model_id} present on {root}")
        return ModelHealth(
            spec.id,
            "unavailable",
            f"{spec.model_id} not pulled (ollama pull {spec.model_id})",
        )

    def _health_openai(self, spec: ModelSpec) -> ModelHealth:
        from models.registry import local_provider_name

        provider = local_provider_name()
        base = _openai_base(provider)
        try:
            body = _http_json(f"{base}/models")
        except (urllib.error.URLError, OSError) as exc:
            return ModelHealth(spec.id, "unavailable", f"no server at {base} ({exc})")
        ids = [str(item.get("id") or "") for item in body.get("data") or []]
        if not ids or spec.model_id in ids:
            return ModelHealth(spec.id, "ok", f"{base} reachable")
        return ModelHealth(
            spec.id,
            "unavailable",
            f"{spec.model_id} not served by {base} (has {', '.join(ids[:3])})",
        )

    def _health_import(self, spec: ModelSpec, module: str) -> ModelHealth:
        if find_spec(module) is None:
            return ModelHealth(spec.id, "unavailable", f"python package '{module}' not installed")
        return ModelHealth(spec.id, "ok", f"{module} available ({spec.model_id})")

    # ── Admission ───────────────────────────────────────────────────────

    def ensure_loaded(self, role: str = ROLE_BRAIN, *, probe: bool = False) -> LoadResult:
        """Make room for ``role`` and report what will serve it.

        Evicts the resident heavy model when a *different* heavy model is
        needed. With ``probe=True`` the runtime is also health-checked.
        """
        selection = select_for_role(role, registry=self.registry)
        if selection is None:
            return LoadResult(None, message=f"no model available for role '{role}'")

        spec = selection.spec
        evicted = ""
        if spec.heavy:
            with self._lock:
                current = self._resident_heavy
                # Compare model ids, not spec ids: two roles can legitimately
                # point at the same weights, and evicting those would only
                # force a needless reload.
                if current is not None and current.model_id != spec.model_id:
                    evicted = current.id
                self._resident_heavy = spec
            if evicted:
                current_spec = self.registry.get(evicted)
                if current_spec is not None:
                    self.unload(current_spec)

        health = self.health(spec) if probe else None
        message = f"role={role} -> {spec.model_id}"
        if evicted:
            message += f" (evicted {evicted})"
        if selection.degraded:
            message += " [substitute]"
        return LoadResult(selection, health, evicted, message)

    def unload(self, spec: ModelSpec) -> bool:
        """Ask the server to release the model's VRAM. Best effort."""
        registry = self.registry
        if registry.effective_runtime(spec) != RUNTIME_OLLAMA or not spec.configured:
            return False
        root = _ollama_root()
        try:
            # keep_alive=0 with an empty prompt is Ollama's unload request.
            _http_json(f"{root}/api/generate", {"model": spec.model_id, "keep_alive": 0})
            logger.info("Unloaded %s from %s", spec.model_id, root)
            return True
        except Exception as exc:
            logger.debug("Unload of %s failed: %s", spec.model_id, exc)
            return False

    def unload_all(self) -> None:
        """Release the resident heavy model, if any."""
        with self._lock:
            current = self._resident_heavy
            self._resident_heavy = None
        if current is not None:
            self.unload(current)


def get_manager() -> ModelManager:
    return ModelManager.get()
