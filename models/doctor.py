"""``immortility doctor`` — preflight report for the model fleet.

Run it as ``python -m models.doctor`` or from the CLI with ``/doctor``.

Exit codes: 0 when everything required is healthy, 1 when a required component
is broken or the config is invalid. Missing *optional* specialists (coder,
vision) are warnings — the agent degrades instead of failing.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass

from models.registry import get_registry, local_provider_name, reset_registry_cache
from models.router import describe_roles, select_for_role
from models.types import ModelConfigError
from models.vram import probe_gpu, probe_ram

OK = "ok"
INFO = "info"
WARN = "warn"
FAIL = "fail"

_SYMBOL = {OK: "[ok]  ", INFO: "[--]  ", WARN: "[warn]", FAIL: "[FAIL]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""

    def line(self) -> str:
        text = f"{_SYMBOL.get(self.status, '[?]   ')} {self.name}"
        if self.detail:
            text += f" — {self.detail}"
        return text


def _hardware_checks() -> list[Check]:
    checks: list[Check] = []
    gpu = probe_gpu(max_age_s=0.0)
    if gpu.available and gpu.vram_total_mb:
        used = int(gpu.vram_used_mb or 0)
        total = int(gpu.vram_total_mb)
        free = int(gpu.vram_free_mb or 0)
        status = WARN if total < 8000 else OK
        checks.append(
            Check(
                "GPU / VRAM",
                status,
                f"{used}/{total} MB used, {free} MB free, util {gpu.gpu_percent or 0:.0f}%",
            )
        )
    else:
        checks.append(
            Check("GPU / VRAM", WARN, gpu.detail or "no NVIDIA GPU detected — CPU only")
        )

    ram = probe_ram()
    if ram.available and ram.total_mb:
        checks.append(
            Check(
                "System RAM",
                OK,
                f"{int(ram.used_mb or 0)}/{int(ram.total_mb)} MB used",
            )
        )
    else:
        checks.append(Check("System RAM", WARN, "psutil unavailable"))

    checks.append(Check("Platform", OK, f"{platform.system()} {platform.release()}, Python {platform.python_version()}"))
    return checks


def _fleet_checks() -> tuple[list[Check], bool]:
    """Per-spec health. Returns (checks, required_failure)."""
    checks: list[Check] = []
    required_failure = False

    registry = get_registry()
    from models.manager import get_manager

    manager = get_manager()

    provider = local_provider_name()
    checks.append(
        Check("Local provider", OK, f"{provider} (IMMORTILITY_LLM_PROVIDER)")
    )

    for spec in sorted(registry.all(), key=lambda s: (s.role, s.priority, s.id)):
        health = manager.health(spec)
        label = f"{spec.role}/{spec.id}"
        detail = f"{spec.model_id or '(unset)'} — {health.detail}" if health.detail else spec.model_id
        if health.ok:
            checks.append(Check(label, OK, detail))
            continue
        if health.state == "inactive":
            checks.append(Check(label, INFO, detail))
            continue
        if spec.optional or health.state == "disabled" or not spec.configured:
            checks.append(Check(label, WARN, detail))
            continue
        checks.append(Check(label, FAIL, detail))
        required_failure = True

    return checks, required_failure


def _routing_checks() -> tuple[list[Check], bool]:
    checks: list[Check] = []
    failure = False

    brain = select_for_role("brain")
    if brain is None:
        checks.append(Check("Route: brain", FAIL, "no chat model configured"))
        failure = True
    else:
        checks.append(
            Check("Route: brain", OK, f"{brain.spec.model_id} (ctx {brain.spec.context})")
        )
        if brain.spec.context > 16384:
            checks.append(
                Check(
                    "Context size",
                    WARN,
                    f"{brain.spec.context} is above the 16384 tested ceiling for 8 GB",
                )
            )

    code = select_for_role("code")
    if code is None:
        checks.append(Check("Route: code", WARN, "no coding model available"))
    else:
        note = " (brain fallback)" if code.degraded else ""
        checks.append(Check("Route: code", OK, f"{code.spec.model_id}{note}"))

    vision = select_for_role("vision")
    if vision is None:
        checks.append(
            Check(
                "Route: vision",
                WARN,
                "unavailable — set OLLAMA_VISION_MODEL to enable image understanding",
            )
        )
    else:
        checks.append(Check("Route: vision", OK, vision.spec.model_id))

    if os.environ.get("IMMORTILITY_NUM_CTX"):
        checks.append(
            Check(
                "IMMORTILITY_NUM_CTX",
                WARN,
                "Ollama reads num_ctx from the Modelfile — recreate the model "
                "(ollama create ... -f scripts/ollama/Modelfile.qwythos9b-q4) for it to apply",
            )
        )

    for role in ("embed", "asr", "tts"):
        selection = select_for_role(role)
        if selection is None:
            checks.append(Check(f"Route: {role}", FAIL, "unavailable"))
            failure = True
        else:
            checks.append(Check(f"Route: {role}", OK, selection.spec.model_id))

    return checks, failure


def run_checks() -> tuple[list[Check], int]:
    """Collect all checks. Returns (checks, exit_code)."""
    checks = _hardware_checks()
    try:
        reset_registry_cache()
        get_registry()
    except ModelConfigError as exc:
        checks.append(Check("config/models.yaml", FAIL, str(exc)))
        return checks, 1

    checks.append(Check("config/models.yaml", OK, f"{len(get_registry().all())} specs"))

    fleet, fleet_failed = _fleet_checks()
    routes, routes_failed = _routing_checks()
    checks.extend(fleet)
    checks.extend(routes)

    exit_code = 1 if (fleet_failed or routes_failed) else 0
    return checks, exit_code


def report() -> tuple[str, int]:
    """Rendered plain-text report plus exit code."""
    checks, exit_code = run_checks()
    lines = ["Immortility doctor — model fleet", ""]
    lines += [check.line() for check in checks]

    roles = describe_roles()
    if roles:
        lines += ["", "Active routing:"]
        lines += [f"  {role:8s} {model}" for role, model in roles.items()]

    warns = sum(1 for c in checks if c.status == WARN)
    fails = sum(1 for c in checks if c.status == FAIL)
    lines += ["", f"{len(checks)} checks, {warns} warnings, {fails} failures"]
    if not fails:
        lines.append("Verdict: usable. Warnings are optional capabilities.")
    else:
        lines.append("Verdict: a required component is broken — see FAIL lines above.")
    return "\n".join(lines), exit_code


def main() -> int:
    text, code = report()
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
