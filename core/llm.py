"""Unified LLM client — Gemini primary, Ollama fallback.

Drop-in compatible with ``ollama.chat`` return shape:
    {"message": {"role": "assistant", "content": "..."}}

Env:
  GEMINI_API_KEY / GOOGLE_API_KEY — enables Gemini
  GEMINI_MODEL — default gemini-2.0-flash (use gemini-2.0-flash-lite for max speed)
  IMMORTILITY_LLM_PROVIDER — gemini | ollama | auto (default auto)
  OLLAMA_MODEL / IMMORTILITY_FALLBACK_MODEL — local fallback model
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DOTENV_LOADED = False
_gemini_client: Any = None
_gemini_client_lock = threading.Lock()
_warmed = False


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
        # Minimal parser if python-dotenv missing
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


def _provider() -> str:
    _load_dotenv()
    raw = (os.environ.get("IMMORTILITY_LLM_PROVIDER") or "auto").strip().lower()
    if raw in {"gemini", "google"}:
        return "gemini" if _gemini_api_key() else "ollama"
    if raw == "ollama":
        return "ollama"
    # auto
    return "gemini" if _gemini_api_key() else "ollama"


def _gemini_model() -> str:
    _load_dotenv()
    return (os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash").strip()


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


def _ollama_model(explicit: str | None = None) -> str:
    _load_dotenv()
    if explicit and explicit not in {"qwen3:14b", "qwen3:8b", "qwen3.5:4b", "auto"}:
        # Keep explicit non-default ollama tags when caller asks
        if ":" in explicit or explicit.startswith("qwen") or explicit.startswith("llama"):
            return explicit
    return (
        os.environ.get("OLLAMA_MODEL")
        or os.environ.get("IMMORTILITY_FALLBACK_MODEL")
        or "qwen3:8b"
    ).strip()


def _ollama_num_ctx() -> int:
    """Keep KV cache small on 8GB laptops — avoids std::bad_alloc on long chats.

    14B+ models need an even tighter default or the CUDA process hard-crashes.
    """
    _load_dotenv()
    raw = (os.environ.get("OLLAMA_NUM_CTX") or "").strip()
    model = (
        os.environ.get("OLLAMA_MODEL")
        or os.environ.get("IMMORTILITY_FALLBACK_MODEL")
        or "qwen3:8b"
    ).lower()
    default = 4096 if any(x in model for x in ("14b", "27b", "32b", "70b")) else 8192
    if not raw:
        return default
    try:
        return max(1024, min(32768, int(raw)))
    except ValueError:
        return default

def _ollama_num_gpu() -> int | None:
    """Optional partial GPU offload. Lower = more layers on CPU/RAM, less VRAM crash risk.

    Set OLLAMA_NUM_GPU in .env (e.g. 20–28 for qwen3:14b on 8GB).
    Empty / unset = let Ollama decide (full GPU when it fits).
    """
    _load_dotenv()
    raw = (os.environ.get("OLLAMA_NUM_GPU") or "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def _is_vram_oom(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "out of memory",
            "out-of-memory",
            "cudamalloc",
            "cuda0 buffer",
            "unable to allocate cuda",
            "ggml_cuda",
            "cuda malloc",
            "bad_alloc",
            "failed to allocate memory",
            "prompt cache",
            "cuda error",
            "shared object initialization failed",
            "0xc0000409",
            "stack-based buffer",
            "llama-server process has terminated",
            "status code: 500",
        )
    )


def _is_cuda_crash(exc: BaseException) -> bool:
    """GPU driver / llama-server hard crashes — retry smaller model on GPU then CPU."""
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "cuda error",
            "shared object initialization failed",
            "0xc0000409",
            "llama-server process has terminated",
            "stack-based buffer",
        )
    )


def _installed_ollama_models() -> set[str]:
    try:
        import subprocess

        proc = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        names: set[str] = set()
        for line in (proc.stdout or "").splitlines()[1:]:
            parts = line.split()
            if parts:
                names.add(parts[0].strip())
        return names
    except Exception:
        return set()


def _light_ollama_candidates(primary: str) -> list[str]:
    """Prefer smaller installed models when the primary OOM's on GPU."""
    preferred = [
        "qwen3:8b",
        "qwen3.5:4b",
        "llama3.2:3b",
        "llama3.2:1b",
        "qwen2.5:3b",
        "phi3:mini",
        "gemma2:2b",
        "qwen2.5:1.5b",
    ]
    installed = _installed_ollama_models()
    if installed:
        return [t for t in preferred if t in installed and t != primary]
    return [t for t in preferred if t != primary]


def active_backend() -> str:
    """Human-readable active backend name."""
    p = _provider()
    if p == "gemini":
        return f"gemini:{_gemini_model()}"
    return f"ollama:{_ollama_model()}"


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
        # Gemini uses user / model
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


def _chat_ollama(
    messages: list[dict[str, Any]],
    model: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    from ollama import chat as ollama_chat

    tag = _ollama_model(model)
    # Drop Immortility-only kwargs Ollama may not understand in older clients
    kwargs = dict(kwargs)
    kwargs.pop("think", None)
    max_tokens = kwargs.pop("max_output_tokens", None)
    temperature = kwargs.pop("temperature", None)
    options = dict(kwargs.pop("options", None) or {})
    options.setdefault("num_ctx", _ollama_num_ctx())
    num_gpu = _ollama_num_gpu()
    if num_gpu is not None:
        options.setdefault("num_gpu", num_gpu)
    if max_tokens:
        options.setdefault("num_predict", int(max_tokens))
    if temperature is not None:
        options.setdefault("temperature", float(temperature))

    def _call(model_tag: str, opts: dict[str, Any]) -> dict[str, Any]:
        call_kwargs = dict(kwargs)
        if opts:
            call_kwargs["options"] = opts
        try:
            return ollama_chat(model=model_tag, messages=messages, think=False, **call_kwargs)
        except TypeError:
            return ollama_chat(model=model_tag, messages=messages, **call_kwargs)

    try:
        return _call(tag, options)
    except Exception as exc:
        recoverable = _is_vram_oom(exc) or _is_cuda_crash(exc)
        if not recoverable:
            raise

        # 0) CUDA hard-crash on 14b → try smaller model on GPU first (faster)
        if _is_cuda_crash(exc) or "14b" in tag:
            for alt in _light_ollama_candidates(tag):
                try:
                    logger.warning(
                        "Ollama GPU crash on %s — trying smaller model %s", tag, alt
                    )
                    return _call(alt, options)
                except Exception as alt_exc:
                    logger.debug("Ollama alt GPU %s failed: %s", alt, alt_exc)

        # 1) Same model on CPU (num_gpu=0)
        logger.warning("Ollama GPU failure on %s — retrying on CPU", tag)
        cpu_opts = {**options, "num_gpu": 0}
        try:
            return _call(tag, cpu_opts)
        except Exception as cpu_exc:
            logger.warning("Ollama CPU retry failed (%s)", cpu_exc)

        # 2) Smaller installed models on CPU
        for alt in _light_ollama_candidates(tag):
            try:
                logger.warning("Ollama recovery — trying %s on CPU", alt)
                return _call(alt, {**options, "num_gpu": 0})
            except Exception as alt_exc:
                logger.debug("Ollama alt %s failed: %s", alt, alt_exc)

        raise RuntimeError(
            f"Local Ollama crashed or ran out of memory loading '{tag}'. "
            f"Your GPU likely cannot run this model stably (14B ≈ 9GB+ VRAM). "
            f"Fix: set OLLAMA_MODEL=qwen3:8b in .env, restart Ollama, close other GPU apps. "
            f"Detail: {exc}"
        ) from exc


def chat(
    model: str = "auto",
    messages: list[dict[str, Any]] | None = None,
    think: bool = False,
    force_provider: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Provider-agnostic chat. Prefers Gemini when configured, else Ollama."""
    messages = messages or []
    fmt = kwargs.pop("format", None)
    json_mode = fmt == "json"
    max_output_tokens = kwargs.pop("max_output_tokens", None)
    temperature = kwargs.pop("temperature", 0.4)
    provider = (force_provider or _provider()).lower()

    if model and "gemini" in str(model).lower() and not force_provider:
        provider = "gemini"

    ollama_kwargs = dict(kwargs)
    if fmt is not None:
        ollama_kwargs["format"] = fmt

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
            # Sibling flash models share the same free-tier quota — don't burn another call
            if not quota_hit:
                current = _gemini_model()
                alt = "gemini-2.0-flash" if "lite" in current else "gemini-2.0-flash-lite"
                if alt != current:
                    try:
                        logger.warning("Gemini %s failed (%s); retry %s", current, exc, alt)
                        result = _chat_gemini(
                            messages,
                            model=alt,
                            json_mode=bool(json_mode),
                            max_output_tokens=max_output_tokens,
                            temperature=float(temperature) if temperature is not None else 0.4,
                        )
                        return result
                    except Exception as exc2:
                        gemini_error = exc2
                        logger.warning("Gemini retry failed (%s); falling back to Ollama", exc2)
                else:
                    logger.warning("Gemini failed (%s); falling back to Ollama", exc)
            else:
                logger.warning("Gemini quota/rate-limit (%s); falling back to Ollama", exc)
        try:
            return _chat_ollama(
                messages,
                model=None,  # use OLLAMA_MODEL / light default — never pass "auto"/gemini ids
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                **ollama_kwargs,
            )
        except Exception as ollama_exc:
            raise RuntimeError(
                f"Gemini unavailable ({gemini_error}); "
                f"Ollama fallback failed ({ollama_exc})"
            ) from ollama_exc

    return _chat_ollama(
        messages,
        model=model if model not in {"auto", None} and "gemini" not in str(model).lower() else None,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        **ollama_kwargs,
    )


_FAST_SYSTEM = (
    "You are Immortility, Reyansh's desktop AI assistant. "
    "Be warm, capable, and concise — 1–4 short sentences unless he asks for detail. "
    "You can open sites/apps, control the PC via tools, list Desktop projects, and help with code. "
    "Never claim to be BERT. If asked what model you are, say you are Immortility "
    "running locally via Ollama (qwen3:8b by default; 14b if your GPU allows) with optional Gemini when available. "
    "Address him as Reyansh. No markdown fences, no long preambles."
)


def fast_chat(
    user_message: str,
    *,
    history: list[dict[str, str]] | None = None,
    system: str | None = None,
    max_output_tokens: int = 220,
    extra_context: str = "",
) -> str:
    """Low-latency conversational reply (HUD chat + talk mode)."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system or _FAST_SYSTEM},
    ]
    if history:
        for m in history[-8:]:
            role = m.get("role") or "user"
            content = (m.get("content") or "").strip()
            if content and role in {"user", "assistant", "model"}:
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
    return str(reply).strip() or "I did not get a response. Try again."


def warm_llm() -> None:
    """Background warmup so the first real reply is not cold-start slow."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    try:
        if _provider() in {"gemini", "google"} and _gemini_api_key():
            _gemini_client_cached()
            # Tiny ping to open the connection / cache model path
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