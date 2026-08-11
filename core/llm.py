"""Unified LLM client — Gemini + OpenAI-compatible local servers.

Drop-in return shape (same as former ollama.chat):
    {"message": {"role": "assistant", "content": "..."}}

Local inference goes through any OpenAI-compatible HTTP endpoint
(vLLM in WSL2 by default, Ollama /v1, or a custom base URL). Backend
is selected by env only — no code changes to swap models/servers.

Env:
  IMMORTILITY_LLM_PROVIDER — vllm | ollama | openai | openai_compat | gemini | auto
  VLLM_BASE_URL / VLLM_MODEL — default http://127.0.0.1:8000/v1 , Qwen/Qwen3-8B
  OLLAMA_BASE_URL / OLLAMA_MODEL — default http://127.0.0.1:11434/v1
  OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL — generic OpenAI-compatible
  GEMINI_API_KEY / GEMINI_MODEL — optional cloud path
  IMMORTILITY_FALLBACK_MODEL — secondary model on the same local base URL
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from core.reply_format import polish_reply

logger = logging.getLogger(__name__)

_DOTENV_LOADED = False
_gemini_client: Any = None
_gemini_client_lock = threading.Lock()
_openai_clients: dict[str, Any] = {}
_openai_clients_lock = threading.Lock()
_warmed = False

DEFAULT_VLLM_BASE = "http://127.0.0.1:8000/v1"
DEFAULT_VLLM_MODEL = "Qwen/Qwen3-8B"
DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434/v1"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"


def _load_dotenv() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None  # type: ignore

    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if load_dotenv and env_path.is_file():
        load_dotenv(env_path, override=False)
    elif env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def _gemini_api_key() -> str:
    _load_dotenv()
    return (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_AI_API_KEY")
        or ""
    ).strip()


def _local_provider_name() -> str:
    """Configured local OpenAI-compatible provider (never gemini)."""
    _load_dotenv()
    raw = (os.environ.get("IMMORTILITY_LLM_PROVIDER") or "auto").strip().lower()
    if raw in {"ollama"}:
        return "ollama"
    if raw in {"openai", "openai_compat"}:
        return "openai"
    if raw in {"vllm", "local"}:
        return "vllm"
    # auto / gemini-without-key / unknown → prefer vLLM
    return "vllm"


def _provider() -> str:
    _load_dotenv()
    raw = (os.environ.get("IMMORTILITY_LLM_PROVIDER") or "auto").strip().lower()
    if raw in {"gemini", "google"}:
        return "gemini" if _gemini_api_key() else _local_provider_name()
    if raw in {"vllm", "local"}:
        return "vllm"
    if raw == "ollama":
        return "ollama"
    if raw in {"openai", "openai_compat"}:
        return "openai"
    # auto
    return "gemini" if _gemini_api_key() else _local_provider_name()


def _gemini_model() -> str:
    _load_dotenv()
    try:
        from core.config import get_config

        fallbacks = get_config().gemini_fallback_models
        default = fallbacks[0] if fallbacks else "gemini-2.0-flash"
    except Exception:
        default = "gemini-2.0-flash"
    return (os.environ.get("GEMINI_MODEL") or default).strip()


def _gemini_client_cached():
    """Reuse one Gemini client — avoids per-request handshake overhead."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    with _gemini_client_lock:
        if _gemini_client is not None:
            return _gemini_client
        from google import genai

        api_key = _gemini_api_key()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _gemini_client = genai.Client(api_key=api_key)
        return _gemini_client


def _is_placeholder_model(name: str | None) -> bool:
    if not name:
        return True
    low = name.strip().lower()
    return low in {"auto", "none", ""} or "gemini" in low


def local_model(explicit: str | None = None, *, provider: str | None = None) -> str:
    """Resolve the chat model id for the active OpenAI-compatible backend."""
    _load_dotenv()
    p = (provider or _local_provider_name()).lower()
    if explicit and not _is_placeholder_model(explicit):
        return explicit.strip()

    if p == "ollama":
        return (
            os.environ.get("OLLAMA_MODEL")
            or os.environ.get("VLLM_MODEL")
            or os.environ.get("IMMORTILITY_FALLBACK_MODEL")
            or DEFAULT_OLLAMA_MODEL
        ).strip()
    if p == "openai":
        return (
            os.environ.get("OPENAI_MODEL")
            or os.environ.get("VLLM_MODEL")
            or os.environ.get("OLLAMA_MODEL")
            or DEFAULT_VLLM_MODEL
        ).strip()
    # vllm / local
    return (
        os.environ.get("VLLM_MODEL")
        or os.environ.get("OLLAMA_MODEL")
        or os.environ.get("IMMORTILITY_FALLBACK_MODEL")
        or DEFAULT_VLLM_MODEL
    ).strip()


# Back-compat alias used by action_engine / older imports
def _ollama_model(explicit: str | None = None) -> str:
    return local_model(explicit)


def _fallback_model(primary: str) -> str | None:
    _load_dotenv()
    alt = (os.environ.get("IMMORTILITY_FALLBACK_MODEL") or "").strip()
    if alt and alt != primary:
        return alt
    return None


def local_base_url(provider: str | None = None) -> str:
    """Base URL for the OpenAI-compatible chat endpoint (includes /v1)."""
    _load_dotenv()
    p = (provider or _local_provider_name()).lower()
    if p == "ollama":
        return (
            os.environ.get("OLLAMA_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or DEFAULT_OLLAMA_BASE
        ).rstrip("/")
    if p == "openai":
        return (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("VLLM_BASE_URL")
            or DEFAULT_VLLM_BASE
        ).rstrip("/")
    return (
        os.environ.get("VLLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or DEFAULT_VLLM_BASE
    ).rstrip("/")


def local_api_key(provider: str | None = None) -> str:
    _load_dotenv()
    p = (provider or _local_provider_name()).lower()
    if p == "openai":
        return (os.environ.get("OPENAI_API_KEY") or "not-needed").strip()
    if p == "ollama":
        return (os.environ.get("OLLAMA_API_KEY") or os.environ.get("OPENAI_API_KEY") or "ollama").strip()
    return (os.environ.get("VLLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "not-needed").strip()


def _openai_client(base_url: str, api_key: str):
    key = f"{base_url}|{api_key}"
    with _openai_clients_lock:
        cached = _openai_clients.get(key)
        if cached is not None:
            return cached
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package required for local LLM. pip install openai"
            ) from exc
        try:
            from core.config import get_config

            timeout = float(get_config().openai_timeout_seconds)
        except Exception:
            timeout = 120.0
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        _openai_clients[key] = client
        return client


def active_backend() -> str:
    """Human-readable active backend name."""
    p = _provider()
    if p == "gemini":
        return f"gemini:{_gemini_model()}"
    return f"{p}:{local_model(provider=p)}@{local_base_url(p)}"


def _messages_to_gemini(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Split system prompt; convert chat messages for google-genai."""
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for m in messages:
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        text = str(content)
        if role == "system":
            system_parts.append(text)
            continue
        g_role = "model" if role in {"assistant", "model"} else "user"
        contents.append({"role": g_role, "parts": [{"text": text}]})
    system = "\n\n".join(system_parts) if system_parts else None
    if not contents:
        contents = [{"role": "user", "parts": [{"text": "Hello"}]}]
    return system, contents


def _chat_gemini(
    messages: list[dict[str, Any]],
    model: str | None = None,
    *,
    json_mode: bool = False,
    max_output_tokens: int | None = None,
    temperature: float = 0.4,
) -> dict[str, Any]:
    try:
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "google-genai package required for Gemini. pip install google-genai"
        ) from exc

    model_name = model if model and "gemini" in model.lower() else _gemini_model()
    system, contents = _messages_to_gemini(messages)
    client = _gemini_client_cached()

    cfg_kwargs: dict[str, Any] = {"temperature": temperature}
    if system:
        cfg_kwargs["system_instruction"] = system
    if json_mode:
        cfg_kwargs["response_mime_type"] = "application/json"
    if max_output_tokens is not None and max_output_tokens > 0:
        cfg_kwargs["max_output_tokens"] = int(max_output_tokens)
    config = types.GenerateContentConfig(**cfg_kwargs)

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=config,
    )
    text = getattr(response, "text", None) or ""
    if not text and getattr(response, "candidates", None):
        try:
            parts = response.candidates[0].content.parts
            text = "".join(getattr(p, "text", "") or "" for p in parts)
        except Exception:
            text = str(response)
    return {"message": {"role": "assistant", "content": text.strip()}}


def _normalize_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        role = (m.get("role") or "user").lower()
        if role == "model":
            role = "assistant"
        content = m.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        out.append({"role": role, "content": str(content)})
    return out


def _hint_for_provider(provider: str) -> str:
    if provider == "ollama":
        return (
            "Is Ollama running with its OpenAI endpoint? "
            "Start `ollama serve` and set OLLAMA_BASE_URL=http://127.0.0.1:11434/v1"
        )
    if provider == "openai":
        return "Check OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL."
    return (
        "Is vLLM running in WSL2? "
        "From WSL: `bash scripts/wsl/start_vllm.sh` "
        f"(expects {DEFAULT_VLLM_BASE})"
    )


def _ollama_api_root(openai_v1_url: str | None = None) -> str:
    """Map OpenAI-compat base (.../v1) to Ollama native root."""
    base = (openai_v1_url or local_base_url("ollama")).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base.rstrip("/")


def _chat_ollama_native(
    messages: list[dict[str, Any]],
    model: str | None = None,
    *,
    max_output_tokens: int | None = None,
    temperature: float = 0.4,
) -> dict[str, Any]:
    """Native Ollama /api/chat with think=false (Qwen3 otherwise empties content)."""
    import json
    import urllib.error
    import urllib.request

    tag = local_model(model, provider="ollama")
    root = _ollama_api_root()
    url = f"{root}/api/chat"
    options: dict[str, Any] = {
        "temperature": float(temperature) if temperature is not None else 0.4,
    }
    if max_output_tokens is not None and int(max_output_tokens) > 0:
        options["num_predict"] = int(max_output_tokens)

    try:
        from core.config import get_config

        think_flag = bool(get_config().ollama_think)
        http_timeout = float(get_config().openai_timeout_seconds)
    except Exception:
        think_flag = False
        http_timeout = 120.0

    payload = {
        "model": tag,
        "messages": _normalize_openai_messages(messages),
        "stream": False,
        "think": think_flag,
        "options": options,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {root} ({exc}). {_hint_for_provider('ollama')}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"Ollama native chat failed on '{tag}' at {root}: {exc}. "
            f"{_hint_for_provider('ollama')}"
        ) from exc

    msg = body.get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text:
        # Extremely defensive: some builds still stash text under thinking keys
        for key in ("thinking", "reasoning"):
            alt = (msg.get(key) or body.get(key) or "").strip()
            if alt and len(alt) < 400 and "\n" not in alt[:80]:
                text = alt
                break
    if not text:
        logger.warning("Ollama returned empty content for model %s", tag)
    return {"message": {"role": "assistant", "content": text}}


def _message_text_from_openai_choice(choice: Any) -> str:
    """Prefer content; never treat long Qwen 'reasoning' traces as the answer."""
    if choice is None or choice.message is None:
        return ""
    msg = choice.message
    text = (getattr(msg, "content", None) or "").strip()
    if text:
        return text
    # Dump for debugging empty replies
    try:
        dumped = msg.model_dump() if hasattr(msg, "model_dump") else {}
        reasoning = (dumped.get("reasoning") or "").strip()
        if reasoning:
            logger.warning(
                "OpenAI-compat reply had empty content and %d chars of reasoning "
                "(Qwen think mode). Prefer Ollama native API with think=false.",
                len(reasoning),
            )
    except Exception:
        pass
    return ""


def _chat_openai_compat(
    messages: list[dict[str, Any]],
    model: str | None = None,
    *,
    provider: str | None = None,
    json_mode: bool = False,
    max_output_tokens: int | None = None,
    temperature: float = 0.4,
    **kwargs: Any,
) -> dict[str, Any]:
    """Chat via any OpenAI-compatible server (vLLM / Ollama / custom)."""
    p = (provider or _local_provider_name()).lower()
    if p in {"local", "vllm"}:
        p = "vllm"

    # Qwen3 via Ollama OpenAI-compat often fills `reasoning` and leaves
    # `content` empty — use native /api/chat with think=false instead.
    if p == "ollama":
        return _chat_ollama_native(
            messages,
            model=model,
            max_output_tokens=max_output_tokens,
            temperature=float(temperature) if temperature is not None else 0.4,
        )

    base = local_base_url(p)
    api_key = local_api_key(p)
    tag = local_model(model, provider=p)
    client = _openai_client(base, api_key)

    kwargs = dict(kwargs)
    kwargs.pop("think", None)
    kwargs.pop("options", None)
    kwargs.pop("format", None)
    extra_body = dict(kwargs.pop("extra_body", None) or {})

    create_kwargs: dict[str, Any] = {
        "model": tag,
        "messages": _normalize_openai_messages(messages),
        "temperature": float(temperature) if temperature is not None else 0.4,
    }
    if max_output_tokens is not None and int(max_output_tokens) > 0:
        create_kwargs["max_tokens"] = int(max_output_tokens)
    if json_mode:
        create_kwargs["response_format"] = {"type": "json_object"}
    if extra_body:
        create_kwargs["extra_body"] = extra_body

    def _call(model_tag: str) -> dict[str, Any]:
        resp = client.chat.completions.create(**{**create_kwargs, "model": model_tag})
        choice = (resp.choices or [None])[0]
        text = _message_text_from_openai_choice(choice)
        return {"message": {"role": "assistant", "content": text}}

    try:
        return _call(tag)
    except Exception as exc:
        err = str(exc).lower()
        connectionish = any(
            s in err
            for s in (
                "connection",
                "connect",
                "refused",
                "timed out",
                "timeout",
                "name or service not known",
                "nodename nor servname",
                "10061",
                "actively refused",
            )
        )
        alt = _fallback_model(tag)
        if alt and not connectionish:
            try:
                logger.warning(
                    "Local LLM %s failed (%s) — retrying fallback model %s",
                    tag,
                    exc,
                    alt,
                )
                return _call(alt)
            except Exception as alt_exc:
                logger.debug("Fallback model %s failed: %s", alt, alt_exc)

        if connectionish:
            raise RuntimeError(
                f"Cannot reach OpenAI-compatible LLM at {base} ({exc}). {_hint_for_provider(p)}"
            ) from exc
        raise RuntimeError(
            f"Local LLM error on model '{tag}' at {base}: {exc}. {_hint_for_provider(p)}"
        ) from exc


# Back-compat name
def _chat_ollama(
    messages: list[dict[str, Any]],
    model: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return _chat_openai_compat(messages, model=model, provider="ollama", **kwargs)


def chat(
    model: str = "auto",
    messages: list[dict[str, Any]] | None = None,
    think: bool = False,
    force_provider: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Provider-agnostic chat. Gemini when configured; else OpenAI-compatible local."""
    del think  # unused — kept for call-site compatibility
    messages = messages or []
    fmt = kwargs.pop("format", None)
    json_mode = fmt == "json"
    max_output_tokens = kwargs.pop("max_output_tokens", None)
    temperature = kwargs.pop("temperature", 0.4)
    provider = (force_provider or _provider()).lower()

    if provider in {"local"}:
        provider = "vllm"

    if model and "gemini" in str(model).lower() and not force_provider:
        provider = "gemini"

    local_kwargs = dict(kwargs)

    if provider in {"gemini", "google"}:
        gemini_error: Exception | None = None
        try:
            result = _chat_gemini(
                messages,
                model=model if "gemini" in str(model).lower() else None,
                json_mode=bool(json_mode),
                max_output_tokens=max_output_tokens,
                temperature=float(temperature) if temperature is not None else 0.4,
            )
            logger.debug("LLM via Gemini (%s)", _gemini_model())
            return result
        except Exception as exc:
            gemini_error = exc
            err_l = str(exc).lower()
            quota_hit = "429" in err_l or "resource_exhausted" in err_l or "quota" in err_l
            if not quota_hit:
                current = _gemini_model()
                try:
                    from core.config import get_config

                    alts = list(get_config().gemini_fallback_models)
                except Exception:
                    alts = ["gemini-2.0-flash", "gemini-2.0-flash-lite"]
                alt = next((a for a in alts if a != current), None)
                if alt:
                    try:
                        logger.warning("Gemini %s failed (%s); retry %s", current, exc, alt)
                        return _chat_gemini(
                            messages,
                            model=alt,
                            json_mode=bool(json_mode),
                            max_output_tokens=max_output_tokens,
                            temperature=float(temperature) if temperature is not None else 0.4,
                        )
                    except Exception as exc2:
                        gemini_error = exc2
                        logger.warning("Gemini retry failed (%s); falling back to local", exc2)
                else:
                    logger.warning("Gemini failed (%s); falling back to local", exc)
            else:
                logger.warning("Gemini quota/rate-limit (%s); falling back to local", exc)
        try:
            return _chat_openai_compat(
                messages,
                model=None,
                provider=_local_provider_name(),
                json_mode=bool(json_mode),
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                **local_kwargs,
            )
        except Exception as local_exc:
            raise RuntimeError(
                f"Gemini unavailable ({gemini_error}); "
                f"local OpenAI-compatible fallback failed ({local_exc})"
            ) from local_exc

    # Force local OpenAI-compatible path
    local_p = provider if provider in {"vllm", "ollama", "openai"} else _local_provider_name()
    explicit = None if _is_placeholder_model(str(model) if model else None) else str(model)
    return _chat_openai_compat(
        messages,
        model=explicit,
        provider=local_p,
        json_mode=bool(json_mode),
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        **local_kwargs,
    )


def _fast_system_prompt() -> str:
    from core.config import load_prompt_file
    from core.reply_format import reply_format_rules

    try:
        from memory.user_profile import get_user_name

        user_name = get_user_name()
    except Exception:
        user_name = "there"
    loaded = load_prompt_file(
        "fast_system.txt",
        user_name=user_name,
        reply_format_rules=reply_format_rules(user_name),
    )
    if loaded.strip():
        return loaded
    return (
        f"You are Immortility, {user_name}'s desktop AI assistant. "
        f"Address the user as {user_name}. Be concise. "
        f"{reply_format_rules(user_name)}"
    )


def fast_chat(
    user_message: str,
    *,
    history: list[dict[str, str]] | None = None,
    system: str | None = None,
    max_output_tokens: int | None = None,
    extra_context: str = "",
) -> str:
    """Low-latency conversational reply (HUD chat + talk mode)."""
    if max_output_tokens is None:
        try:
            from core.config import get_config

            max_output_tokens = get_config().fast_chat_max_tokens
        except Exception:
            max_output_tokens = 220
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system or _fast_system_prompt()},
    ]
    if history:
        for m in history[-16:]:
            role = m.get("role") or "user"
            content = (m.get("content") or "").strip()
            if content and role in {"user", "assistant", "model"}:
                if role == "assistant" and len(content) > 1200:
                    content = content[:1199].rstrip() + "…"
                messages.append({"role": role, "content": content})
    if extra_context:
        messages.append(
            {
                "role": "user",
                "content": f"(Context — use if relevant)\n{extra_context[:2500]}",
            }
        )
    messages.append({"role": "user", "content": user_message})
    result = chat(
        model="auto",
        messages=messages,
        think=False,
        max_output_tokens=max_output_tokens,
        temperature=0.35,
    )
    reply = (result.get("message") or {}).get("content") or ""
    reply = polish_reply(str(reply))
    if reply:
        return reply
    return (
        "I got an empty reply from the local model. "
        "Try again — thinking/reasoning mode may need to be disabled for this backend."
    )


def warm_llm() -> None:
    """Background warmup so the first real reply is not cold-start slow."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    try:
        if _provider() in {"gemini", "google"} and _gemini_api_key():
            _gemini_client_cached()
            chat(
                model="auto",
                messages=[
                    {"role": "system", "content": "Reply with OK only."},
                    {"role": "user", "content": "ping"},
                ],
                think=False,
                max_output_tokens=4,
                temperature=0.0,
            )
            logger.info("LLM warmed (%s)", active_backend())
    except Exception as exc:
        logger.debug("LLM warm skipped: %s", exc)
        _warmed = False
