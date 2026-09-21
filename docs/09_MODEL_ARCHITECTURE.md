# 09 — Model Architecture

## Design Principle

The model layer answers one question: **which model serves this role right now, on this hardware?**

The model is not the identity of Immortality. It is a replaceable component. The harness, memory, tools, and permissions define what Immortality is. The model provides the intelligence that reasons within that structure.

**The model is never called directly.** All calls go through `core/llm.py::chat()`, which resolves the provider, selects the model, manages VRAM, and handles fallback.

---

## Current Model Fleet

Defined in `config/models.yaml`. The spec declares capability, VRAM requirements, and runtime. Environment variables override any model_id.

### Brain Role — Primary Reasoning

**Spec: `brain` (primary)**
- Runtime: `openai_compat` (NVIDIA NIM)
- Model: `moonshotai/kimi-k3`
- API: `https://integrate.api.nvidia.com/v1`
- Capabilities: chat, reasoning, planning, tools, code
- VRAM: 0 (cloud API)
- Context: ~128K tokens
- Status: ⚠️ **Configured, inactive** — requires `NVIDIA_API_KEY`

**Spec: `brain-vllm` (alternative)**
- Runtime: `openai_compat` (WSL2 vLLM)
- Model: `Qwen/Qwen3-8B`
- VRAM: 6000 MB (heavy)
- Status: Optional, requires WSL2 vLLM server

**Spec: `brain-fallback`**
- Runtime: auto (active provider)
- Model: `IMMORTILITY_FALLBACK_MODEL` env var
- Priority: 90 (used last)
- Status: Optional

### Code Role

**Spec: `coder`**
- Model: `OLLAMA_CODE_MODEL` env var (unset → brain fallback)
- VRAM: 5200 MB (heavy)
- Recommended: `qwen2.5-coder:7b-instruct-q4_K_M`
- Status: Optional, unset by default

**Spec: `coder-large`**
- Model: `IMMORTILITY_CODE_LARGE_MODEL` env var
- VRAM: 19000 MB (gated — never selected on 8GB)
- Status: Disabled by hardware gate

### Vision Role

**Spec: `vision`**
- Model: `OLLAMA_VISION_MODEL` env var (unset → unavailable)
- VRAM: 6800 MB (heavy, evicts brain)
- Recommended: `qwen2.5vl:7b-instruct-q4_K_M`
- Status: ⚠️ **Configured, inactive** — `OLLAMA_VISION_MODEL` not set

### Supporting Roles

| Role | Model | Runtime | VRAM | Status |
|---|---|---|---|---|
| `embed` | `BAAI/bge-small-en-v1.5` | sentence_transformers | 140 MB CPU | ✅ Active |
| `rerank` | `BAAI/bge-reranker-base` | sentence_transformers | 0 CPU | ⚠️ Disabled |
| `asr` | `faster-whisper small.en` | faster_whisper | 0 CPU | ✅ Active |
| `tts` | `sapi5` | sapi | 0 CPU | ✅ Active |

---

## Model Selection Algorithm

When a model call is made:

```
chat(messages, role="brain")
    ↓
1. Explicit model arg? → use verbatim
    ↓
2. Role specialist in registry? → select_for_role(role)
   - Check if spec is configured (model_id not empty)
   - Check if spec is enabled (enabled_env not disabled)
   - Check if runtime matches active provider
   - Check VRAM: heavy → total VRAM; light → free VRAM
   - If checks pass → use this spec
   - If checks fail → record skip reason, try next spec
    ↓
3. Environment variable for provider? → OLLAMA_MODEL, NVIDIA_MODEL, etc.
    ↓
4. Registry brain spec for active provider
    ↓
5. Built-in default (provider-specific hardcoded fallback)
```

The selection is logged. If a specialist is degraded (brain used for code), the log notes this.

---

## VRAM Policy (RTX 4060, 8 GB)

These rules are enforced by `models/manager.py`:

1. **One heavy model resident at a time.** Heavy = `vram_mb >= 3000 MB`. Switching heavy roles evicts the current one via `keep_alive=0` Ollama request.

2. **Heavy models are admitted against total VRAM.** The manager assumes eviction will free enough room. This means a 6GB model can be loaded even if 4GB is currently occupied — because eviction will free it first.

3. **Light models (embed, rerank, ASR, TTS) stay on CPU.** They never compete with the chat model for GPU memory.

4. **`coder-large` (30B class) has a 19GB gate.** It is registered but will never be selected on 8GB. Explicitly for machines with more VRAM.

5. **Vision model evicts the brain.** When a vision task arrives, the brain is unloaded and the vision model loads. After the vision task, the brain must reload on the next request (cold start, ~15-30 seconds on 8GB).

6. **No auto-download.** The manager never calls `ollama pull`. A missing model is reported as `unavailable` and the system degrades gracefully.

---

## Provider Architecture

```
core/llm.py::chat()
    ↓
_provider() → "nvidia" | "ollama" | "vllm" | "openai" | "gemini"
    ↓
┌─────────────────────────────────────────────────────┐
│                                                     │
│  nvidia/vllm/openai → _chat_openai_compat()         │
│  (uses openai Python package, OpenAI-compat API)    │
│                                                     │
│  ollama → _chat_ollama_native()                     │
│  (uses native /api/chat endpoint, think=false)      │
│  (OpenAI-compat endpoint has issues with Qwen3)     │
│                                                     │
│  gemini → _chat_gemini()                            │
│  (uses google-genai SDK, different message format)  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### Why Ollama Gets Native API
Qwen3 via Ollama's OpenAI-compat endpoint sometimes fills `reasoning` and leaves `content` empty. The native `/api/chat` with `think=false` avoids this. Other providers don't have this issue.

### Provider Selection
```
IMMORTILITY_LLM_PROVIDER = nvidia | vllm | ollama | openai | gemini | auto

auto → gemini if GEMINI_API_KEY is set, else vllm
```

---

## Kimi K3 — Target Primary Intelligence

### Why Kimi K3

| Property | Kimi K3 (NVIDIA NIM) | Qwythos-9B Q4 (Ollama) |
|---|---|---|
| Context window | ~128K tokens | 8192 (16384 tested max) |
| Reasoning quality | State of the art | Good for 9B Q4 |
| Tool calling | OpenAI-compat function calling | JSON tool loop |
| Latency | Cloud — network dependent | Local — ~1-3s TTFT |
| Privacy | Cloud — data leaves machine | Local — fully private |
| VRAM cost | 0 (cloud API) | 6+ GB |
| Cost | API credits | Electricity |
| Availability | Requires NVIDIA API key | Offline capable |

### When to Use Kimi K3
- Complex reasoning tasks
- Multi-step planning
- Difficult coding problems
- Long-context document analysis
- When the 8K context of local models is insufficient

### When to Use Local Models
- Privacy-sensitive tasks (anything involving personal data)
- Fast chat / simple Q&A (fast_chat path stays local)
- Offline operation
- When NVIDIA API quota is limited

### Routing Decision
The current architecture does not route automatically between Kimi K3 and local models based on task complexity. This is a future improvement (Phase 5+). Currently, the provider is set globally in `.env`.

---

## Streaming

All providers support streaming token output:
- `core/llm.py::stream_chat()` — returns assembled text after streaming via `on_token` callback
- `core/execution_kernel.py::run_model()` — accepts `on_token` callback for live display
- HUD receives token stream via polling (not true WebSocket streaming yet)

---

## Tool Calling via Models

The current implementation uses a custom JSON tool loop:
- The agent system prompt describes available tools
- The LLM generates: `{"tool": "...", "args": {...}}`
- The harness parses, validates, and executes

This is not the same as OpenAI function calling or Kimi K3's built-in tool calling.

**Future improvement:** Use the model's native function calling format for providers that support it (NVIDIA/OpenAI). This would produce more reliable tool call structure and fewer JSON parsing failures.

---

## Multimodal Input

### Current State
- Text: all providers
- Image: vision model only (unset by default)
- Audio: Whisper (separate, not passed to LLM)
- Video: not supported

### Vision Input Format
When the vision model is active, images are passed as base64-encoded content in the message:
```python
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
    ]
}]
```
This is the OpenAI-compat multimodal format, supported by Qwen-VL via Ollama.

---

## Health Monitoring

`models/doctor.py` provides a comprehensive health report:
- GPU: model, VRAM total/free/used
- RAM: total/used
- Per-spec health: ok | unconfigured | disabled | inactive | unavailable | error
- Active routing table: which model would serve each role right now
- Configuration validity: missing required models

Run with: `python -m models.doctor` or `/doctor` in CLI.

---

## Future Model Considerations

### BGE-M3 Embeddings
Currently: `BAAI/bge-small-en-v1.5` (384 dimensions, 130 MB, CPU)
Recommended later: `BAAI/bge-M3` (1024 dimensions, better multilingual and code retrieval)

**Migration cost:** Changing the embedding model invalidates ALL existing TurboVec vectors. A full reindex of ~14k chunks is required. This is a planned Phase 6 migration.

### Reranker
`BAAI/bge-reranker-base` is configured but disabled by default (`RERANK_ENABLED=0`). Enable when retrieval quality needs improvement at the cost of ~100ms latency per query.

### Voice Model (ASR)
Whisper `small.en` is the default. Upgrade options:
- `small` → multilingual support
- `medium.en` → better accuracy, ~3x memory
- `large-v3` → best accuracy, ~6GB, CUDA required

CUDA Whisper competes with the chat model for VRAM. Default stays CPU.
