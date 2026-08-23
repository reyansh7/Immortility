# Immortility — Phase status

Honest status of the freeze. Features listed as roadmap are **not** implemented.

| Slice | Deliverable | Status |
| --- | --- | --- |
| 0 | Audit | shipped (`IMMORTALITY_AUDIT.md`) |
| 1 | Model registry / router / VRAM / doctor | shipped (`models/`, `config/models.yaml`) |
| H | Harness Foundation (traces, TTFT, last-turn) | shipped (`core/harness.py`) |
| K | Execution kernel, FAST/AGENT/BACKGROUND, streaming, warm/cold routing, FAST context discipline | shipped (`core/execution_kernel.py`, `core/execution_mode.py`) |
| 2A | Capability honesty (card/report, denials, search states, URL-preserving research) | shipped (`core/capabilities.py`) |
| 2B | Git / documents / Docker / DBs / command hardening | shipped (`tools/git_tool.py`, `tools/document_tool.py`, `tools/docker_tool.py`, `tools/database_tool.py`; command engine `tools/command_tool.py`) |
| 3 | Autonomous coding loop (Planner→Coder→Executor→Debugger→Reviewer→Reflector) over Tool Kernel + Execution Kernel | started — slice 3.0 (`core/coding_engine.py`); Phase 3 is not complete (no ECC skills/hooks, no fresh-context review) |
| 4 | Fresh-context reviewer, ECC skills / rules / hooks as roles | not started |
| 5 | Multimodal uploads (VL / OCR / video / audio) | not started — vision remains unconfigured unless `OLLAMA_VISION_MODEL` is set |
| 6 | Memory/RAG budgets, experience; BGE-M3 only with full reindex | not started — still BGE-small |
| 7 | Full evaluation harness (25 references + novel-task generalization) | not started — Slice H only records traces |
| 8–10 | Training data, LoRA, continuous improve | not started |

Recommended later (env only, no auto-download): `OLLAMA_CODE_MODEL` = a Qwen3-Coder 8B-class tag that fits 8 GB; `OLLAMA_VISION_MODEL` = Qwen3-VL-8B-class. `coder-large` stays gated.
