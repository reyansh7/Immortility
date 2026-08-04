"""OpenAI-compatible LLM provider selection (no live server required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_llm_caches(monkeypatch):
    import core.llm as llm

    llm._DOTENV_LOADED = True  # skip reading real .env
    llm._openai_clients.clear()
    llm._gemini_client = None
    yield
    llm._openai_clients.clear()


def test_provider_vllm_default_without_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("IMMORTILITY_FALLBACK_MODEL", raising=False)
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "auto")
    monkeypatch.setenv("VLLM_MODEL", "Qwen/Qwen3-8B")
    from core.llm import _provider, local_base_url, local_model

    assert _provider() == "vllm"
    assert "8000" in local_base_url("vllm")
    assert local_model(provider="vllm") == "Qwen/Qwen3-8B"


def test_provider_ollama_base_url(monkeypatch):
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    from core.llm import local_base_url, local_model, _provider

    assert _provider() == "ollama"
    assert local_base_url() == "http://127.0.0.1:11434/v1"
    assert local_model() == "qwen3:8b"


def test_provider_openai_compat(monkeypatch):
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9000/v1")
    monkeypatch.setenv("OPENAI_MODEL", "Devstral-Small")
    from core.llm import local_base_url, local_model, _provider

    assert _provider() == "openai"
    assert local_base_url() == "http://127.0.0.1:9000/v1"
    assert local_model() == "Devstral-Small"


def test_chat_openai_compat_mocked(monkeypatch):
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "vllm")
    monkeypatch.setenv("VLLM_MODEL", "Qwen/Qwen3-8B")
    monkeypatch.setenv("VLLM_BASE_URL", "http://127.0.0.1:8000/v1")

    fake_choice = MagicMock()
    fake_choice.message.content = "hello from vllm"
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch("core.llm._openai_client", return_value=fake_client):
        from core.llm import chat

        out = chat(
            model="auto",
            messages=[{"role": "user", "content": "hi"}],
            force_provider="vllm",
            max_output_tokens=32,
        )
    assert out["message"]["content"] == "hello from vllm"
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "Qwen/Qwen3-8B"
    assert kwargs["max_tokens"] == 32


def test_active_backend_string(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "vllm")
    monkeypatch.setenv("VLLM_MODEL", "Qwen/Qwen3-8B")
    from core.llm import active_backend

    assert active_backend().startswith("vllm:")
    assert "Qwen" in active_backend()
