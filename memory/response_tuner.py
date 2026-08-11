"""Optional adaptive token budgets (disabled by default).

Enable with IMMORTILITY_RESPONSE_TUNER=1 after core learning loops are stable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()


def tuner_enabled() -> bool:
    raw = (os.environ.get("IMMORTILITY_RESPONSE_TUNER") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass
class ResponseMetrics:
    route: str
    mode: str
    avg_response_length: float = 0.0
    samples: int = 0
    user_asked_shorter: int = 0
    user_asked_longer: int = 0
    preferred_format: str = "mixed"


def _metrics_path() -> Path:
    try:
        from core.repo_paths import get_repo_root

        d = get_repo_root() / ".immortility"
    except Exception:
        d = Path(".immortility")
    d.mkdir(parents=True, exist_ok=True)
    return d / "response_metrics.json"


class ResponseTuner:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _metrics_path()
        self._data: dict[str, dict] = {}
        self._load()

    def _key(self, route: str, mode: str) -> str:
        return f"{route}|{mode}"

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}

    def _save(self) -> None:
        with _LOCK:
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get_metrics(self, route: str, mode: str) -> ResponseMetrics:
        raw = self._data.get(self._key(route, mode)) or {}
        return ResponseMetrics(
            route=route,
            mode=mode,
            avg_response_length=float(raw.get("avg_response_length") or 0),
            samples=int(raw.get("samples") or 0),
            user_asked_shorter=int(raw.get("user_asked_shorter") or 0),
            user_asked_longer=int(raw.get("user_asked_longer") or 0),
            preferred_format=str(raw.get("preferred_format") or "mixed"),
        )

    def observe_user_feedback(self, text: str, route: str = "CHAT", mode: str = "text") -> None:
        if not tuner_enabled():
            return
        low = (text or "").lower()
        m = self.get_metrics(route, mode)
        if re.search(r"\b(too long|be brief|shorter|tl;dr)\b", low):
            m.user_asked_shorter += 1
        if re.search(r"\b(explain more|elaborate|too short|more detail)\b", low):
            m.user_asked_longer += 1
        self._data[self._key(route, mode)] = asdict(m)
        self._save()

    def get_adaptive_token_limit(self, route: str, mode: str = "text") -> int:
        try:
            from core.config import get_config

            cfg = get_config()
            base = {
                "CHAT": cfg.cli_chat_max_tokens,
                "ACTION": cfg.action_summary_max_tokens,
                "PROJECT": cfg.cli_chat_max_tokens,
                "WEB": cfg.cli_chat_max_tokens,
            }.get(route, cfg.cli_chat_max_tokens)
        except Exception:
            base = 320

        if not tuner_enabled():
            return base

        metrics = self.get_metrics(route, mode)
        if metrics.user_asked_shorter > metrics.user_asked_longer:
            base = int(base * 0.7)
        elif metrics.user_asked_longer > metrics.user_asked_shorter:
            base = int(base * 1.3)
        if mode == "voice":
            base = int(base * 0.5)
        return max(64, min(base, 2000))


_TUNER: ResponseTuner | None = None


def get_response_tuner() -> ResponseTuner:
    global _TUNER
    if _TUNER is None:
        _TUNER = ResponseTuner()
    return _TUNER
