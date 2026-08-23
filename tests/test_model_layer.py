"""Phase 1 model layer — registry parsing, VRAM gating, role routing, doctor.

No live GPU or inference server is required: the GPU probe is monkeypatched and
health probes are only exercised through their non-network branches.
"""

from __future__ import annotations

import textwrap

import pytest

from models.registry import ModelRegistry, load_registry, reset_registry_cache
from models.router import (
    describe_roles,
    fallback_model_id,
    model_id_for_role,
    select_for_role,
)
from models.types import ModelConfigError
from models.vram import GpuSnapshot
from models import vram as vram_module

FLEET = textwrap.dedent(
    """
    version: 1
    defaults:
      total_vram_mb: 8188
      heavy_threshold_mb: 3000
      context: 8192
    models:
      - id: brain
        role: brain
        runtime: ollama
        model_id: qwythos9b-q4
        env_model_var: auto
        capabilities: [chat, reasoning, code]
        priority: 10
        vram_mb: 6200
        heavy: true
      - id: brain-fallback
        role: brain
        runtime: auto
        model_id: ""
        env_model_var: IMMORTILITY_FALLBACK_MODEL
        capabilities: [chat, code]
        priority: 90
        vram_mb: 5600
        optional: true
      - id: coder
        role: code
        runtime: auto
        model_id: ""
        env_model_var: OLLAMA_CODE_MODEL
        capabilities: [chat, code]
        priority: 5
        vram_mb: 5200
        optional: true
      - id: coder-large
        role: code
        runtime: auto
        model_id: ""
        env_model_var: IMMORTILITY_CODE_LARGE_MODEL
        capabilities: [chat, code]
        priority: 1
        vram_mb: 19000
        min_free_vram_mb: 19000
        optional: true
      - id: vision
        role: vision
        runtime: auto
        model_id: ""
        env_model_var: OLLAMA_VISION_MODEL
        capabilities: [vision, chat]
        priority: 10
        vram_mb: 6800
        optional: true
      - id: rerank
        role: rerank
        runtime: sentence_transformers
        model_id: BAAI/bge-reranker-base
        enabled_env: RERANK_ENABLED
        capabilities: [rerank]
        device: cpu
    """
)


@pytest.fixture
def fleet_file(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text(FLEET, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Neutral environment: Ollama provider, no specialists configured."""
    for var in (
        "OLLAMA_MODEL",
        "VLLM_MODEL",
        "OPENAI_MODEL",
        "IMMORTILITY_FALLBACK_MODEL",
        "OLLAMA_CODE_MODEL",
        "OLLAMA_VISION_MODEL",
        "IMMORTILITY_CODE_LARGE_MODEL",
        "IMMORTILITY_NUM_CTX",
        "RERANK_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "ollama")
    import core.llm as llm

    llm._DOTENV_LOADED = True  # never read the real .env during tests
    reset_registry_cache()
    vram_module.reset_cache()
    yield
    reset_registry_cache()
    vram_module.reset_cache()


@pytest.fixture
def gpu_8gb(monkeypatch):
    """An RTX 4060: 8188 MB total, mostly free."""
    snapshot = GpuSnapshot(
        available=True, gpu_percent=3.0, vram_used_mb=400.0, vram_total_mb=8188.0
    )
    monkeypatch.setattr(vram_module, "_run_nvidia_smi", lambda: snapshot)
    monkeypatch.setattr("models.router.probe_gpu", lambda *a, **k: snapshot)
    return snapshot


# ── Registry parsing ────────────────────────────────────────────────────


def test_registry_parses_specs(fleet_file):
    registry = load_registry(fleet_file)

    assert len(registry.all()) == 6
    brain = registry.get("brain")
    assert brain is not None
    assert brain.model_id == "qwythos9b-q4"
    assert brain.context == 8192
    assert brain.heavy is True
    # heavy specs default their admission gate to their own footprint
    assert brain.min_free_vram_mb == 6200
    assert registry.get("coder").configured is False


def test_env_overrides_model_id(fleet_file, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "some-other-tag")
    monkeypatch.setenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b")

    registry = load_registry(fleet_file)

    assert registry.get("brain").model_id == "some-other-tag"
    assert registry.get("coder").model_id == "qwen2.5-coder:7b"


def test_ollama_tag_does_not_leak_into_vllm_spec(fleet_file, monkeypatch):
    """An Ollama tag must never satisfy a spec pinned to another runtime."""
    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "vllm")
    monkeypatch.setenv("OLLAMA_MODEL", "qwythos9b-q4")

    registry = load_registry(fleet_file)

    # brain is pinned to the ollama runtime, so under vLLM it cannot serve.
    assert registry.serves_provider(registry.get("brain")) is False


def test_num_ctx_override_is_clamped(fleet_file, monkeypatch):
    monkeypatch.setenv("IMMORTILITY_NUM_CTX", "16384")
    assert load_registry(fleet_file).get("brain").context == 16384

    monkeypatch.setenv("IMMORTILITY_NUM_CTX", "1000000")
    assert load_registry(fleet_file).get("brain").context == 32768


def test_enabled_env_gates_spec(fleet_file, monkeypatch):
    registry = load_registry(fleet_file)
    rerank = registry.get("rerank")

    assert registry.is_disabled(rerank) is True
    monkeypatch.setenv("RERANK_ENABLED", "1")
    assert registry.is_disabled(rerank) is False


@pytest.mark.parametrize(
    "body, message",
    [
        ("version: 1\ndefaults: {}\n", "non-empty 'models' list"),
        (
            "version: 1\nmodels:\n  - id: a\n    role: r\n    runtime: nope\n"
            "    capabilities: [chat]\n",
            "unknown runtime",
        ),
        (
            "version: 1\nmodels:\n  - id: a\n    role: r\n    runtime: auto\n"
            "    capabilities: [telepathy]\n",
            "unknown capabilities",
        ),
        (
            "version: 1\nmodels:\n  - id: a\n    role: r\n    runtime: auto\n"
            "    capabilities: [chat]\n  - id: a\n    role: r\n    runtime: auto\n"
            "    capabilities: [chat]\n",
            "duplicate model id",
        ),
    ],
)
def test_invalid_config_raises(tmp_path, body, message):
    path = tmp_path / "bad.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ModelConfigError) as exc:
        load_registry(path)
    assert message in str(exc.value)


# ── Routing under an 8 GB budget ────────────────────────────────────────


def test_brain_is_selected_for_chat(fleet_file, gpu_8gb):
    registry = load_registry(fleet_file)

    selection = select_for_role("brain", registry=registry)

    assert selection is not None
    assert selection.spec.id == "brain"
    assert selection.degraded is False


def test_coding_specialist_wins_when_installed(fleet_file, gpu_8gb, monkeypatch):
    monkeypatch.setenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b")
    registry = load_registry(fleet_file)

    selection = select_for_role("code", registry=registry)

    assert selection.spec.id == "coder"
    assert selection.degraded is False


def test_code_degrades_to_brain_when_no_specialist(fleet_file, gpu_8gb):
    registry = load_registry(fleet_file)

    selection = select_for_role("code", registry=registry)

    assert selection is not None
    assert selection.spec.id == "brain"
    assert selection.degraded is True
    assert any("coder" in reason for reason in selection.skipped)


def test_30b_coder_is_refused_on_8gb(fleet_file, gpu_8gb, monkeypatch):
    monkeypatch.setenv("IMMORTILITY_CODE_LARGE_MODEL", "qwen3-coder:30b")
    registry = load_registry(fleet_file)

    selection = select_for_role("code", registry=registry)

    assert selection.spec.id != "coder-large"
    assert any("coder-large" in reason and "VRAM" in reason for reason in selection.skipped)


def test_brain_role_ignores_coding_specialist(fleet_file, gpu_8gb, monkeypatch):
    """A coder declaring `chat` must not take over orchestration."""
    monkeypatch.setenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b")
    registry = load_registry(fleet_file)

    assert select_for_role("brain", registry=registry).spec.id == "brain"


def test_vision_unavailable_without_model(fleet_file, gpu_8gb):
    registry = load_registry(fleet_file)

    assert select_for_role("vision", registry=registry) is None
    assert model_id_for_role("vision", registry=registry) == ""


def test_vision_model_never_substitutes_for_text(fleet_file, gpu_8gb, monkeypatch):
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b")
    registry = load_registry(fleet_file)

    assert select_for_role("vision", registry=registry).spec.id == "vision"
    # The VL model declares `chat`, but text roles must not silently use it.
    assert select_for_role("brain", registry=registry).spec.id == "brain"


def test_fallback_skips_the_primary(fleet_file, gpu_8gb, monkeypatch):
    monkeypatch.setenv("IMMORTILITY_FALLBACK_MODEL", "qwen3:8b")
    registry = load_registry(fleet_file)

    assert (
        fallback_model_id(primary="qwythos9b-q4", registry=registry) == "qwen3:8b"
    )


def test_describe_roles_reports_disabled_and_unavailable(fleet_file, gpu_8gb):
    registry = load_registry(fleet_file)

    roles = describe_roles(registry)

    assert roles["brain"] == "qwythos9b-q4"
    assert roles["vision"] == "unavailable"
    assert "RERANK_ENABLED" in roles["rerank"]


def test_no_gpu_falls_back_to_configured_budget(fleet_file, monkeypatch):
    """Without a GPU the declared budget still admits the brain."""
    snapshot = GpuSnapshot(available=False, detail="no nvidia-smi")
    monkeypatch.setattr("models.router.probe_gpu", lambda *a, **k: snapshot)
    registry = load_registry(fleet_file)

    assert select_for_role("brain", registry=registry).spec.id == "brain"


# ── Manager admission ───────────────────────────────────────────────────


def test_manager_evicts_previous_heavy_model(fleet_file, gpu_8gb, monkeypatch):
    monkeypatch.setenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b")
    registry = load_registry(fleet_file)

    from models.manager import ModelManager

    manager = ModelManager(registry)
    unloaded: list[str] = []
    monkeypatch.setattr(
        manager, "unload", lambda spec: unloaded.append(spec.model_id) or True
    )

    first = manager.ensure_loaded("brain")
    assert first.model_id == "qwythos9b-q4"
    assert first.evicted == ""
    assert manager.resident_heavy().id == "brain"

    second = manager.ensure_loaded("vision")
    assert second.model_id == "qwen3-vl:8b"
    assert second.evicted == "brain"
    assert unloaded == ["qwythos9b-q4"]


def test_manager_does_not_evict_same_weights(fleet_file, gpu_8gb, monkeypatch):
    """brain and code resolve to one model here — no pointless reload."""
    registry = load_registry(fleet_file)

    from models.manager import ModelManager

    manager = ModelManager(registry)
    unloaded: list[str] = []
    monkeypatch.setattr(
        manager, "unload", lambda spec: unloaded.append(spec.model_id) or True
    )

    manager.ensure_loaded("brain")
    result = manager.ensure_loaded("code")

    assert result.evicted == ""
    assert unloaded == []


def test_health_reports_unconfigured_and_inactive(fleet_file, monkeypatch):
    registry = load_registry(fleet_file)

    from models.manager import ModelManager

    manager = ModelManager(registry)

    unconfigured = manager.health(registry.get("vision"))
    assert unconfigured.state == "unconfigured"
    assert "OLLAMA_VISION_MODEL" in unconfigured.detail

    disabled = manager.health(registry.get("rerank"))
    assert disabled.state == "disabled"

    monkeypatch.setenv("IMMORTILITY_LLM_PROVIDER", "vllm")
    inactive = manager.health(registry.get("brain"))
    assert inactive.state == "inactive"


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ModelConfigError):
        load_registry(tmp_path / "absent.yaml")


# ── Repository config and doctor ────────────────────────────────────────


def test_repo_config_is_valid_and_covers_core_roles():
    registry = load_registry()

    assert isinstance(registry, ModelRegistry)
    for role in ("brain", "code", "vision", "embed", "rerank", "asr", "tts"):
        assert registry.for_role(role), f"role '{role}' missing from config/models.yaml"


def test_doctor_invalid_yaml_exits_nonzero(monkeypatch):
    """Malformed fleet config is a hard failure, not a warning."""
    from models.types import ModelConfigError
    from models import doctor as doctor_mod

    monkeypatch.setattr(
        doctor_mod,
        "get_registry",
        lambda: (_ for _ in ()).throw(ModelConfigError("broken yaml")),
    )
    monkeypatch.setattr(doctor_mod, "reset_registry_cache", lambda: None)

    from models.doctor import run_checks

    checks, code = run_checks()
    assert code == 1
    assert any(c.status == "fail" and "broken yaml" in c.detail for c in checks)


def test_doctor_runs_without_a_server(monkeypatch):
    """Doctor must produce a report even when nothing is reachable."""
    snapshot = GpuSnapshot(available=False, detail="no nvidia-smi")
    monkeypatch.setattr(vram_module, "_run_nvidia_smi", lambda: snapshot)
    monkeypatch.setattr("models.router.probe_gpu", lambda *a, **k: snapshot)

    import urllib.request

    def _refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _refuse)

    from models.doctor import report

    text, code = report()

    assert "Immortility doctor" in text
    assert "Route: brain" in text
    # A missing chat server is a real failure, not a warning.
    assert code == 1


def test_llm_role_resolution_prefers_registry_specialist(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwythos9b-q4")
    monkeypatch.setenv("OLLAMA_CODE_MODEL", "qwen2.5-coder:7b")
    reset_registry_cache()

    from core.llm import local_model, role_model

    assert local_model() == "qwythos9b-q4"
    assert local_model(role="code") == "qwen2.5-coder:7b"
    assert role_model("vision") == ""
    # An explicit id always wins over role routing.
    assert local_model("pinned-model", role="code") == "pinned-model"
