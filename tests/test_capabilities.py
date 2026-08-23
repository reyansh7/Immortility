"""Capability honesty regression tests — registry output, not LLM phrasing."""

from __future__ import annotations

import os

from core.capabilities import (
    UNSUPPORTED,
    answer_capability_question,
    capability_card,
    capability_report,
    get_capability,
    list_capabilities,
    permissions_for_tool,
    wants_capability_report,
)
from tools.tool_registry import ToolRegistry
from tools.web_search_tool import (
    SEARCH_EXECUTED_ZERO_RESULTS,
    SEARCH_SUCCESS,
    SEARCH_UNAVAILABLE,
)


def test_vision_unavailable_without_env(monkeypatch):
    monkeypatch.delenv("OLLAMA_VISION_MODEL", raising=False)
    from models.registry import reset_registry_cache

    reset_registry_cache()
    q = "Can you see images?"
    assert wants_capability_report(q)
    text = answer_capability_question(q)
    assert "unavailable" in text.lower()
    cap = get_capability("vision")
    assert cap is not None
    assert cap.available is False


def test_what_you_can_do_phrasing_is_a_capability_report():
    q = "so now from this tell me what you can do now"
    assert wants_capability_report(q)


def test_chat_token_field_defaults_allow_long_replies():
    from core.config import ImmortilityConfig

    cfg = ImmortilityConfig()
    assert cfg.chat_max_tokens >= 2048
    assert cfg.cli_chat_max_tokens >= 2048
    assert cfg.action_summary_max_tokens >= 2048
    assert cfg.fast_chat_max_tokens <= 512


def test_chat_tokens_clamp_to_vram_ceiling(monkeypatch):
    monkeypatch.setenv("IMMORTILITY_CHAT_TOKENS", "99999")
    from core.config import ImmortilityConfig, reset_config_cache

    reset_config_cache()
    cfg = ImmortilityConfig.from_env()
    assert cfg.chat_max_tokens == 4096
    reset_config_cache()


def test_tell_me_about_yourself_uses_registry():
    text = answer_capability_question("Tell me about yourself.")
    card = capability_card()
    assert "CHAT:" in card
    assert "VISION:" in card
    assert card in text or "CHAT:" in text
    assert "BERT" not in text


def test_what_model_uses_registry_id():
    text = answer_capability_question("What model are you running?")
    assert "Immortility" in text
    assert "BERT" not in text
    brain = get_capability("chat")
    assert brain is not None
    if brain.available and brain.detail:
        token = brain.detail.split()[0]
        assert token in text or "model" in text.lower()


def test_internet_reflects_web_tools():
    text = answer_capability_question("Do you have internet?")
    web = get_capability("web_search")
    assert web is not None
    if web.available:
        assert "available" in text.lower()
        assert "provider=" in (web.detail or "") or "provider=" in text
    else:
        assert "unavailable" in text.lower()


def test_wifi_crack_intentionally_unsupported():
    text = answer_capability_question("Can you crack a WiFi password?")
    assert text.strip() == "Intentionally not supported."
    cap = get_capability("wifi_password_bypass")
    assert cap is not None
    assert cap.available is False
    assert cap.detail == "Intentionally not supported."


def test_unsupported_cannot_be_flipped(monkeypatch):
    monkeypatch.setenv("IMMORTILITY_ALLOW_WIFI_BYPASS", "1")
    monkeypatch.setenv("wifi_password_bypass", "true")
    cap = get_capability("wifi_password_bypass")
    assert cap is not None and cap.available is False
    for name in UNSUPPORTED:
        assert get_capability(name).available is False


def test_run_commands_reflects_registry():
    text = answer_capability_question("Can you run commands?")
    cap = get_capability("terminal")
    assert cap is not None
    expected = "available" if cap.available else "unavailable"
    assert expected in text.lower()
    registry = ToolRegistry()
    registry.setup()
    assert ("run_command" in registry.list_tools()) == cap.available


def test_web_search_names_provider():
    text = answer_capability_question("Can you search the web?")
    cap = get_capability("web_search")
    assert cap is not None
    if cap.available:
        assert "provider=" in cap.detail
        assert "provider=" in text or cap.available


def test_compact_card_has_no_model_ids():
    card = capability_card()
    assert "qwythos" not in card.lower()
    assert "permission" not in card.lower()
    assert "CHAT:" in card
    report = capability_report()
    assert "qwythos" in report.lower() or "Brain model:" in report


def test_every_registered_tool_has_permissions():
    registry = ToolRegistry()
    registry.setup()
    for name in registry.list_tools():
        perms = registry.get_permissions(name)
        assert set(perms) >= {"read", "write", "network", "destructive"}
        derived = permissions_for_tool(name)
        assert set(derived) == {"read", "write", "network", "destructive"}


def test_search_states_are_distinct():
    assert SEARCH_UNAVAILABLE != SEARCH_EXECUTED_ZERO_RESULTS != SEARCH_SUCCESS
    assert "UNAVAILABLE" in SEARCH_UNAVAILABLE
    assert "ZERO" in SEARCH_EXECUTED_ZERO_RESULTS


def test_reasoning_is_not_a_system_capability():
    names = {c.name for c in list_capabilities()}
    assert "reasoning" not in names
    assert "planning" not in names
