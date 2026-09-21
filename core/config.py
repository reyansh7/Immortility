"""Unified Immortility configuration — env overlays defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_int_clamped(name: str, default: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, _env_int(name, default)))


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class ImmortilityConfig:
    """Runtime knobs — override via environment variables."""

    # Chat / tokens
    # FAST stays short (greetings). chat_max_tokens is the long-reply budget
    # (HUD AGENT, CLI chat, reports). 4096 is the 8GB VRAM ceiling.
    fast_chat_max_tokens: int = 220
    chat_max_tokens: int = 2048
    speech_max_tokens: int = 200
    cli_fast_chat_max_tokens: int = 280
    cli_chat_max_tokens: int = 2048
    action_summary_max_tokens: int = 2048
    fast_chat_char_limit: int = 140
    cmd_timeout_seconds: float = 60.0
    cmd_max_output_chars: int = 200_000

    # History / memory
    max_history: int = 30
    max_conversation_entries: int = 200

    # Action engine
    action_max_steps_readonly: int = 10
    action_max_steps_edit: int = 20
    decision_max_retries: int = 3

    # LLM HTTP
    openai_timeout_seconds: float = 120.0
    ollama_think: bool = False

    # Hermes native agent/harness API.  Secrets remain environment-only.
    agent_backend: str = "hermes"
    hermes_base_url: str = "http://127.0.0.1:8642"
    hermes_api_key: str = ""
    hermes_timeout_seconds: float = 300.0
    hermes_poll_seconds: float = 0.75

    # Logging
    log_max_bytes: int = 5_000_000
    log_backup_count: int = 3

    # Learning / critic
    outcome_learning: bool = True
    critic_enabled: bool = True

    # Phase 4 permission mode: safe | assisted | autonomous | developer
    permission_mode: str = "assisted"

    # Web search
    web_search_provider: str = "auto"
    web_search_max_results: int = 5

    # Vector store compression defaults (overridden by embedder when available)
    embedding_dim: int = 384
    bit_width: int = 4

    # Gemini fallbacks (env GEMINI_MODEL is primary)
    gemini_fallback_models: tuple[str, ...] = field(
        default_factory=lambda: ("gemini-2.0-flash", "gemini-2.0-flash-lite")
    )

    @classmethod
    def from_env(cls) -> ImmortilityConfig:
        # Ensure .env is visible
        try:
            from core.llm import _load_dotenv

            _load_dotenv()
        except Exception:
            pass

        chat_tokens = _env_int_clamped("IMMORTILITY_CHAT_TOKENS", 2048, 256, 4096)
        cfg = cls(
            fast_chat_max_tokens=_env_int_clamped("IMMORTILITY_FAST_CHAT_TOKENS", 220, 64, 512),
            chat_max_tokens=chat_tokens,
            speech_max_tokens=_env_int_clamped("IMMORTILITY_SPEECH_TOKENS", 200, 32, 400),
            cli_fast_chat_max_tokens=_env_int_clamped("IMMORTILITY_CLI_FAST_TOKENS", 280, 64, 512),
            cli_chat_max_tokens=_env_int_clamped("IMMORTILITY_CLI_CHAT_TOKENS", chat_tokens, 256, 4096),
            action_summary_max_tokens=_env_int_clamped(
                "IMMORTILITY_ACTION_SUMMARY_TOKENS", chat_tokens, 256, 4096
            ),
            fast_chat_char_limit=_env_int("IMMORTILITY_FAST_CHAT_CHARS", 140),
            cmd_timeout_seconds=_env_float("IMMORTILITY_CMD_TIMEOUT", 60.0),
            cmd_max_output_chars=_env_int("IMMORTILITY_CMD_MAX_OUTPUT", 200_000),
            max_history=_env_int("IMMORTILITY_MAX_HISTORY", 30),
            max_conversation_entries=_env_int("IMMORTILITY_MAX_CONV_ENTRIES", 200),
            action_max_steps_readonly=_env_int("IMMORTILITY_ACTION_STEPS_RO", 10),
            action_max_steps_edit=_env_int("IMMORTILITY_ACTION_STEPS_EDIT", 20),
            decision_max_retries=_env_int("IMMORTILITY_MAX_RETRIES", 3),
            openai_timeout_seconds=_env_float("IMMORTILITY_LLM_TIMEOUT", 120.0),
            ollama_think=_env_bool("IMMORTILITY_OLLAMA_THINK", False),
            agent_backend=(os.environ.get("IMMORTILITY_AGENT_BACKEND") or "hermes").strip().lower(),
            hermes_base_url=(os.environ.get("HERMES_BASE_URL") or "http://127.0.0.1:8642").strip(),
            hermes_api_key=(os.environ.get("HERMES_API_KEY") or os.environ.get("API_SERVER_KEY") or "").strip(),
            hermes_timeout_seconds=_env_float("HERMES_TIMEOUT_SECONDS", 300.0),
            hermes_poll_seconds=_env_float("HERMES_POLL_SECONDS", 0.75),
            log_max_bytes=_env_int("IMMORTILITY_LOG_MAX_BYTES", 5_000_000),
            log_backup_count=_env_int("IMMORTILITY_LOG_BACKUPS", 3),
            outcome_learning=_env_bool("IMMORTILITY_OUTCOME_LEARNING", True),
            critic_enabled=_env_bool("IMMORTILITY_CRITIC", True),
            permission_mode=(
                os.environ.get("IMMORTILITY_PERMISSION_MODE") or "assisted"
            ).strip().lower(),
            web_search_provider=(
                os.environ.get("WEB_SEARCH_PROVIDER") or "auto"
            ).strip().lower(),
            web_search_max_results=_env_int("WEB_SEARCH_MAX_RESULTS", 5),
            embedding_dim=_env_int("IMMORTILITY_EMBEDDING_DIM", 384),
            bit_width=_env_int("IMMORTILITY_BIT_WIDTH", 4),
        )
        return cfg


@lru_cache(maxsize=1)
def get_config() -> ImmortilityConfig:
    return ImmortilityConfig.from_env()


def reset_config_cache() -> None:
    get_config.cache_clear()


def load_prompt_file(name: str, **fmt: str) -> str:
    """Load prompts/<name> and format with {user_name}, {repo_root}, etc."""
    from core.repo_paths import get_repo_root

    path = get_repo_root() / "prompts" / name
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, ValueError):
            return text
    return text
