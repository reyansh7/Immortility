# Web-capable self-learning Immortility

## What changed

Immortility now closes the learn → retrieve → act loops and can search the live web with citations.

### Learning loops

| Loop | Store | Actuator |
|------|--------|----------|
| Experiences | `.immortility/experience.json` | Injected into action engine + RAG action/routing context |
| KG reflections | knowledge graph SQLite | Injected into workflow PLANNING / EDIT_PLANNER |
| Outcomes | `.immortility/outcomes.db` | Scored from next user turn; lessons injected into chat |
| Web research notes | TurboVec `documentation` | Retrieved via `get_learned_context` |
| Self-reflection | experience + KG + TurboVec + JSONL export | After DONE / workflow EXPERIENCE_UPDATE |

### Web research

- `search_google` / `web_search` use provider cascade: **Tavily → Brave → SearXNG → DuckDuckGo Playwright**
- `ResearchAgent` reads multiple pages, synthesizes with citations, fail-closed when no sources
- Shared `core/source_router.py` chooses LOCAL / MEMORY / WEB / TOOLS (CLI + HUD)
- `core/critic.py` blocks unsupported specific claims in WEB mode (one retry max)

### Optional response tuner

`IMMORTILITY_RESPONSE_TUNER=0` by default. When enabled, adjusts token budgets from “too long/short” feedback.

---

## Environment variables

```
WEB_SEARCH_PROVIDER=auto
TAVILY_API_KEY=
BRAVE_API_KEY=
SEARXNG_URL=
WEB_SEARCH_MAX_RESULTS=5
IMMORTILITY_OUTCOME_LEARNING=1
IMMORTILITY_CRITIC=1
IMMORTILITY_RESPONSE_TUNER=0
```

Copy from [`.env.example`](../.env.example).

---

## How to test

```powershell
cd C:\Users\reyan\OneDrive\Desktop\immortility1
$env:PYTHONPATH="."
python -c "from tests.test_experience_injection import *; test_format_experiences_block_contains_fix(); test_action_context_override_includes_experiences_shape(); test_format_reflections_block_prefers_overlap(); print('ok')"
python -c "from tests.test_outcome_memory import *; test_score_explicit_positive_negative(); test_score_rephrase_is_negative(); test_record_and_retrieve_lessons(); test_pending_score_flow(); print('ok')"
python -c "from tests.test_web_search_provider import *; test_normalize_results_shape(); test_resolve_provider_auto_prefers_tavily(); print('ok')"
python -c "from tests.test_source_router import *; from tests.test_research_synthesis import *; test_source_router_web_vs_tools_vs_local(); test_research_synthesis_citations_present(); test_research_synthesis_empty_sources_no_hallucination(); print('ok')"
python -c "from tests.test_critic import *; test_critic_flags_unsupported_web_claims(); test_critic_ok_with_sources(); test_apply_critic_fail_closed_no_retry(); print('ok')"
python -c "from tests.test_self_reflection import *; test_experience_dataset_append(); test_response_tuner_disabled_by_default(); print('ok')"
```

### Manual smoke

1. **No API keys:** ask Immortility to research something — DDG fallback should still return results when Playwright works.
2. **With `TAVILY_API_KEY`:** same ask should use Tavily (`provider` in tool result).
3. **Recency ask** in HUD (`what's the latest Next.js release`) → WEB path → answer includes URLs.
4. Say a wrong correction (`wrong, I meant OAuth`) after a reply → next similar ask gets a PAST MISTAKES lesson.
5. After a coding DONE, check `.immortility/experience.json` and optionally `experience_dataset.jsonl`.

Restart Immortility after pulling these changes so HUD/CLI load the new modules.
