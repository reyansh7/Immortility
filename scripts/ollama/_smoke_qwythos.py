"""One-shot Qwythos smoke: chat + JSON tool shape. Do not print secrets."""

from __future__ import annotations

import os
import time
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, val = line.split("=", 1)
    os.environ.setdefault(key.strip(), val.strip())

from core.llm import active_backend, chat, local_model  # noqa: E402

print("OLLAMA_MODEL", os.environ.get("OLLAMA_MODEL"))
print("FALLBACK", os.environ.get("IMMORTILITY_FALLBACK_MODEL"))
print("backend", active_backend())
print("local_model", local_model())

t0 = time.perf_counter()
r = chat(
    messages=[
        {
            "role": "user",
            "content": "Reply with exactly the two letters OK. No other words.",
        }
    ],
    temperature=0.6,
    max_output_tokens=128,
)
chat_ms = int((time.perf_counter() - t0) * 1000)
content = (r.get("message") or {}).get("content") or ""
print("CHAT_MS", chat_ms)
print("CHAT_LEN", len(content))
print("CHAT_PREVIEW", repr(content[:400]))

t0 = time.perf_counter()
r2 = chat(
    messages=[
        {
            "role": "system",
            "content": (
                "Respond with ONE JSON object only: "
                '{"tool":"run_command","args":{"cmd":"echo immortility-smoke"}}'
            ),
        },
        {
            "role": "user",
            "content": "Run a harmless echo command to prove tool JSON works.",
        },
    ],
    temperature=0.4,
    max_output_tokens=256,
)
json_ms = int((time.perf_counter() - t0) * 1000)
c2 = (r2.get("message") or {}).get("content") or ""
print("JSON_MS", json_ms)
print("JSON_LEN", len(c2))
print("JSON_PREVIEW", repr(c2[:600]))

ok_chat = "ok" in content.lower() and bool(content.strip())
has_json = "run_command" in c2 and "echo" in c2.lower()
print("SMOKE_CHAT", ok_chat)
print("SMOKE_JSON", has_json)
print("SMOKE_EMPTY", not content.strip())
