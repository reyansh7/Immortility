# Immortility — Vision

The package and env prefix stay `immortility` / `IMMORTILITY_*`. Spoken name: Immortality.

This document is the product goal. It does **not** claim every layer below is shipped.
See [IMMORTALITY_PHASES.md](IMMORTALITY_PHASES.md) for what exists today.

## The product

Immortility is a **personal AI execution system**, not a chatbot and not 25 apps.

You say what you want in natural language. The system decides how to do it by composing available models, tools, context, memory, planning, execution, verification, and recovery.

**The model is not the product. The system is the product.**

**Composition, not enumeration.** Build a small number of powerful primitives. Do not add a new hardcoded agent for each kind of request. A previously unseen task must reuse existing primitives in a new combination.

Forbidden:

- `if task == PDF → PDF agent`
- `if task == resume → resume agent`
- `if task == browser → browser agent`

The 25 reference projects are a **Reference Capability / Evaluation Matrix** only: evidence that the architecture generalizes, not the specification and not the capability ceiling.

## Performance Contract

Every interactive operation must:

- Minimize time-to-first-useful-response (not only time-to-first-token)
- Avoid unnecessary model and tool calls
- Stream intermediate results
- Run independent operations concurrently
- Keep frequently used models warm when hardware permits
- Never make simple chat pay for the full agent loop

FAST chat should feel immediate. Expensive work runs asynchronously and streams progress. This laptop (RTX 4060, 8 GB VRAM) will not match a cloud ChatGPT backend on every workload; the interactive path still can feel fast.

## Execution strategies (not capability categories)

| Strategy | When | Path |
| --- | --- | --- |
| FAST | Simple Q&A, short generation | Router → kernel → warm brain → stream |
| AGENT | Tools, edits, multi-step | Thin planner only if needed → tools → verify |
| BACKGROUND | Long jobs | Immediate ack + worker + progress |

A new request picks a strategy from intent, complexity, modality, required capabilities, and latency — never from a product-name table.

## Architecture (target)

Experience → Session → Fast Intent/Complexity Router → FAST | AGENT | BACKGROUND → Execution Kernel → Context / Model / Agent-roles / Tools → stream.

The kernel is capability-agnostic: model, tool, and task execution plus cancel, timeout, retry, trace, cache, permissions, concurrency, progress. It does not know the 25 references.

The Model Router is dynamic: required capability, model capability, latency, warm/cold, VRAM, context, modality, complexity. It does not map `pdf`/`resume`/`browser` to fixed models.

ECC-style names (Planner, Researcher, Coder, Executor, Reviewer, Verifier, Reflector) are **roles**, invoked only when required.

On 8 GB, “warm pool” means keep the brain resident. Embeddings, ASR, and TTS stay on CPU. Never auto-pull 30B or VL weights.

## Safety

Memory is data, not policy. Unauthorized WiFi bypass, packet injection, deauth, and credential theft are intentionally not supported — not “because the model has no network.”
