"""Value types for the Immortility model layer.

A ``ModelSpec`` describes one entry of the fleet: which runtime serves it, what
it can do, and how much VRAM it wants. Nothing here loads weights or talks to a
server — see ``models.manager`` for that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ── Capabilities the router understands ─────────────────────────────────
CAP_CHAT = "chat"
CAP_REASONING = "reasoning"
CAP_PLANNING = "planning"
CAP_TOOLS = "tools"
CAP_CODE = "code"
CAP_VISION = "vision"
CAP_EMBED = "embed"
CAP_RERANK = "rerank"
CAP_ASR = "asr"
CAP_TTS = "tts"

KNOWN_CAPABILITIES = frozenset(
    {
        CAP_CHAT,
        CAP_REASONING,
        CAP_PLANNING,
        CAP_TOOLS,
        CAP_CODE,
        CAP_VISION,
        CAP_EMBED,
        CAP_RERANK,
        CAP_ASR,
        CAP_TTS,
    }
)

# ── Runtimes ────────────────────────────────────────────────────────────
# "auto" defers to IMMORTILITY_LLM_PROVIDER so a single YAML entry works for
# both the Ollama and vLLM deployments of the same weights.
RUNTIME_AUTO = "auto"
RUNTIME_OLLAMA = "ollama"
RUNTIME_OPENAI_COMPAT = "openai_compat"
RUNTIME_SENTENCE_TRANSFORMERS = "sentence_transformers"
RUNTIME_FASTER_WHISPER = "faster_whisper"
RUNTIME_SAPI = "sapi"

KNOWN_RUNTIMES = frozenset(
    {
        RUNTIME_AUTO,
        RUNTIME_OLLAMA,
        RUNTIME_OPENAI_COMPAT,
        RUNTIME_SENTENCE_TRANSFORMERS,
        RUNTIME_FASTER_WHISPER,
        RUNTIME_SAPI,
    }
)

# Runtimes served over HTTP by a local inference server.
SERVER_RUNTIMES = frozenset({RUNTIME_AUTO, RUNTIME_OLLAMA, RUNTIME_OPENAI_COMPAT})

HealthState = Literal[
    "ok",
    "unconfigured",
    "disabled",
    # Configured, but its runtime is not the active provider — not an error.
    "inactive",
    "unavailable",
    "error",
    "unknown",
]


class ModelConfigError(RuntimeError):
    """Raised when config/models.yaml is malformed."""


@dataclass(frozen=True)
class ModelSpec:
    """One fleet member. Immutable; env overrides are applied at load time."""

    id: str
    role: str
    runtime: str
    model_id: str
    capabilities: frozenset[str]
    # Lower sorts first — a dedicated specialist outranks the generalist brain.
    priority: int = 100
    context: int = 8192
    vram_mb: int = 0
    # Refuse selection unless at least this much VRAM is free. Defaults to
    # vram_mb for heavy models via ModelRegistry.
    min_free_vram_mb: int = 0
    heavy: bool = False
    optional: bool = False
    device: str = ""
    dimension: int = 0
    env_model_var: str = ""
    enabled_env: str = ""
    notes: str = ""

    @property
    def configured(self) -> bool:
        """True when a concrete model id is set (empty means "not installed")."""
        return bool((self.model_id or "").strip())

    @property
    def server_backed(self) -> bool:
        """True when an HTTP inference server serves this spec."""
        return self.runtime in SERVER_RUNTIMES

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def describe(self) -> str:
        caps = ",".join(sorted(self.capabilities))
        return f"{self.id} [{self.runtime}] {self.model_id or '(unset)'} caps={caps}"


@dataclass(frozen=True)
class ModelHealth:
    """Result of a non-destructive reachability probe."""

    spec_id: str
    state: HealthState
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


@dataclass(frozen=True)
class Selection:
    """Router verdict for one capability request."""

    spec: ModelSpec
    capability: str
    reason: str
    # True when the preferred specialist was skipped and this is a substitute.
    degraded: bool = False
    skipped: tuple[str, ...] = ()

    @property
    def model_id(self) -> str:
        return self.spec.model_id
