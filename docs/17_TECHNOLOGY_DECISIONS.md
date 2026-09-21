# 17 — Technology Decisions

Every technology in this table was evaluated against Immortality's actual requirements. The decision reflects the smallest correct solution, not the most impressive-sounding one.

---

## Intelligence / Model Layer

### Kimi K3 via NVIDIA NIM
| Attribute | Value |
|---|---|
| **Decision** | USE — primary reasoning brain |
| **Problem solved** | 128K context, superior reasoning vs local 9B models, cloud API means no VRAM cost |
| **What it replaces** | Qwythos-9B Q4 as the default brain |
| **Alternative** | Local Ollama models (Qwen3-8B, etc.) |
| **Why alternative is inferior** | 8K context limit, weaker reasoning, slower inference |
| **Tradeoffs** | Requires internet, requires API key, costs credits, data leaves machine |
| **Operational cost** | NVIDIA API credit per token |
| **Recommendation** | Set `NVIDIA_API_KEY`, keep local as fallback for private tasks |

### Ollama
| Attribute | Value |
|---|---|
| **Decision** | KEEP — local inference runtime |
| **Problem solved** | Offline-capable inference, no API costs, data stays local |
| **Tradeoffs** | Smaller context, weaker reasoning than Kimi K3 |
| **Recommendation** | Keep as primary local provider and fallback when NVIDIA unavailable |

### Hermes Backend
| Attribute | Value |
|---|---|
| **Decision** | KEEP AS OPTIONAL — the adapter is complete, the server doesn't exist locally |
| **Problem solved** | Would provide a closed-loop external agent runtime |
| **Current status** | Client adapter complete, external server missing |
| **Action** | Change default `agent_backend` from "hermes" to "legacy" until server exists. Keep the adapter. |
| **Alternative** | Build a minimal local Hermes-compatible gateway wrapping execute_action() |

### LangGraph
| Attribute | Value |
|---|---|
| **Decision** | DO NOT USE |
| **Why not** | The existing workflow_engine.py + execution_kernel.py already provides a custom state machine with pause/resume/retry/checkpoint. LangGraph would replace working code with a framework dependency. |
| **What it would replace** | workflow_engine.py — code we own and can modify |
| **Alternative is inferior because** | We already have it, and it is tailored to our needs |

---

## Retrieval / Knowledge

### TurboVec (current vector store)
| Attribute | Value |
|---|---|
| **Decision** | KEEP — working, ~14k chunks indexed |
| **Problem solved** | Fast hybrid BM25 + semantic retrieval for code |
| **What it replaced** | ChromaDB (migrated in a previous phase) |
| **Alternative** | Qdrant, ChromaDB, pgvector |
| **Why alternatives are inferior** | Already migrated, working correctly, migration would cost reindex of 14k chunks |
| **Recommendation** | Keep indefinitely until a specific capability gap appears |

### BGE-small-en-v1.5 (embeddings)
| Attribute | Value |
|---|---|
| **Decision** | KEEP for now, MIGRATE to BGE-M3 in Phase 6 |
| **Problem solved** | 384-dimension embeddings, CPU-friendly, ~130MB |
| **When to migrate** | When multilingual support or higher-dimensional retrieval is needed |
| **Migration cost** | Full reindex of all TurboVec vectors — ~14k chunks |
| **Alternative** | BGE-M3 (1024-dim, multilingual, better code retrieval) |

### BM25 (`rank-bm25`)
| Attribute | Value |
|---|---|
| **Decision** | KEEP — already part of hybrid search |
| **Problem solved** | Keyword retrieval to complement semantic search |
| **Alternative** | Pure semantic search |
| **Why keep** | Hybrid > pure semantic for code retrieval (identifiers are keywords) |

### tree-sitter (AST parsing)
| Attribute | Value |
|---|---|
| **Decision** | KEEP — used for AST-aware chunking and code graph |
| **Problem solved** | Code-structure-aware retrieval (chunk by function/class, not by line count) |
| **Alternative** | Line-based chunking |
| **Why keep** | AST-aware chunks are dramatically better for code retrieval |

---

## Storage

### SQLite (workflow.db, knowledge_graph.db, outcomes.db)
| Attribute | Value |
|---|---|
| **Decision** | KEEP — correct for personal AI scale |
| **Problem solved** | Persistent structured storage for workflows, facts, outcomes |
| **Alternative** | PostgreSQL |
| **Why PostgreSQL is unnecessary** | Personal AI on one machine. Hundreds to thousands of rows, not millions. No multi-user concurrency. SQLite is faster and simpler at this scale. |
| **Operational cost** | Zero — no server process |

### PostgreSQL
| Attribute | Value |
|---|---|
| **Decision** | DO NOT ADD until a specific need appears |
| **Why not** | No concurrent writes from multiple processes. No multi-user access. SQLite handles the data volume. |

### pgvector
| Attribute | Value |
|---|---|
| **Decision** | DO NOT ADD |
| **Why not** | TurboVec already handles semantic search. Adding PostgreSQL + pgvector would require migrating ~14k chunks and running a Postgres server. |

---

## Browser and Computer Control

### Playwright
| Attribute | Value |
|---|---|
| **Decision** | KEEP — already working for browser automation |
| **Problem solved** | Cross-browser automation with accessibility tree access |
| **Alternative** | Selenium, Puppeteer |
| **Why keep** | Already in use, accessibility-tree based (no coordinates), Python-native |

### pywinauto (Windows UI Automation)
| Attribute | Value |
|---|---|
| **Decision** | ADD in Phase 2 |
| **Problem solved** | Accessibility-tree-based control of native Windows applications |
| **Alternative** | PyAutoGUI (coordinate-based), OpenCV (image-based) |
| **Why pywinauto** | Uses Windows UIA COM API → reliable, resolution-independent, works without screen visible |
| **Why not PyAutoGUI** | Coordinate-based — breaks across DPI/resolution/window position |
| **Why not OpenCV** | Requires template images, fragile, unnecessary for UI automation |
| **Dependency** | `pip install pywinauto` |

### mss (screen capture)
| Attribute | Value |
|---|---|
| **Decision** | ADD in Phase 2 |
| **Problem solved** | Fast, cross-monitor screenshot capture |
| **Alternative** | PIL.ImageGrab, win32api |
| **Why mss** | Faster than PIL.ImageGrab, supports multi-monitor, simple API |

---

## Infrastructure

### Redis
| Attribute | Value |
|---|---|
| **Decision** | DO NOT ADD |
| **Why not** | The event monitoring system needs an asyncio.Queue at most. Adding Redis adds a server process, operational complexity, and persistence overhead for a personal AI that processes events at extremely low volume. |

### Celery
| Attribute | Value |
|---|---|
| **Decision** | DO NOT ADD |
| **Why not** | Background tasks are handled by `execution_kernel.run_task()` via ThreadPoolExecutor. Celery is for distributed worker fleets. |

### Temporal
| Attribute | Value |
|---|---|
| **Decision** | DO NOT ADD |
| **Why not** | Temporal is a durable execution platform for microservices. Immortality's durable task needs are satisfied by SQLite (tasks.db) + checkpoint_manager.py. Temporal would require running two additional services (temporal-server + temporal-ui). |

### NATS / Kafka
| Attribute | Value |
|---|---|
| **Decision** | DO NOT ADD |
| **Why not** | In-process event bus (core/event_bus.py) handles all coordination needs. Distributed message brokers solve multi-process communication problems that don't exist here. |

### Docker
| Attribute | Value |
|---|---|
| **Decision** | KEEP for sandboxed code execution (future), DO NOT containerize Immortality itself |
| **Why not containerize** | Voice I/O, Windows UIA, screen capture require direct hardware access. These don't work inside a container. |
| **Use for** | Optional sandboxed code execution of untrusted scripts (Phase 10) |

---

## Frameworks and Development

### FastAPI
| Attribute | Value |
|---|---|
| **Decision** | CONSIDER for Phase 6+ if HUD API grows significantly |
| **Current state** | stdlib `http.server` handles current endpoint count (< 20 endpoints) |
| **When to switch** | If endpoint count > 30, or if API documentation becomes important, or if async streaming support is needed |
| **Alternative** | Current stdlib server |

### OpenTelemetry
| Attribute | Value |
|---|---|
| **Decision** | DO NOT ADD |
| **Why not** | JSONL event log + harness.py provides sufficient observability for a single-process personal AI. OpenTelemetry is for distributed systems with many services. |
| **Alternative** | Structured JSONL logging (current) |

### MCP (Model Context Protocol)
| Attribute | Value |
|---|---|
| **Decision** | DEFER to Phase 6+ |
| **Problem it solves** | Standardized tool protocol for external services |
| **Why not now** | All current tools run in-process. No external service integration needed yet. MCP adds subprocess management overhead. |
| **When to add** | When integrating GitHub API, Google Calendar, Notion, etc. |

---

## Voice

### faster-whisper
| Attribute | Value |
|---|---|
| **Decision** | KEEP — working, offline, accurate |
| **Problem solved** | Offline speech-to-text |
| **Alternative** | OpenAI Whisper API, Azure Speech, Google STT |
| **Why keep** | Local, no API cost, no privacy concern |

### pyttsx3 + pywin32 (SAPI)
| Attribute | Value |
|---|---|
| **Decision** | KEEP — working, offline, interruptible |
| **Problem solved** | Offline text-to-speech with barge-in |
| **Alternative** | Kokoro (high quality local TTS), ElevenLabs |
| **When to upgrade** | When voice quality becomes a priority. Kokoro at the quality/latency tradeoff. |

---

## Decision Matrix Summary

| Technology | Decision | Reason |
|---|---|---|
| Kimi K3 / NVIDIA NIM | ✅ USE | Best reasoning, 128K context |
| Ollama | ✅ KEEP | Local inference, offline fallback |
| Hermes backend | ✅ KEEP (optional) | Adapter is correct; server is missing |
| LangGraph | ❌ DON'T ADD | We own the harness |
| TurboVec | ✅ KEEP | Working, no benefit to replacing |
| BGE-small | ✅ KEEP → BGE-M3 later | Phase 6 migration |
| SQLite | ✅ KEEP | Right scale for personal AI |
| PostgreSQL | ❌ DON'T ADD | Unnecessary at this scale |
| pgvector | ❌ DON'T ADD | TurboVec handles it |
| Playwright | ✅ KEEP | Browser automation, working |
| pywinauto | ✅ ADD Phase 2 | Windows app control |
| mss | ✅ ADD Phase 2 | Screen capture |
| Redis | ❌ DON'T ADD | asyncio Queue is sufficient |
| Celery | ❌ DON'T ADD | ThreadPoolExecutor handles it |
| Temporal | ❌ DON'T ADD | SQLite + checkpoints handle it |
| Docker | ⚠️ KEEP for sandboxing | Phase 10 code execution |
| FastAPI | ⚠️ CONSIDER Phase 6+ | Current server is sufficient |
| OpenTelemetry | ❌ DON'T ADD | JSONL log is sufficient |
| MCP | ⚠️ DEFER Phase 6+ | Not needed for current tools |
| faster-whisper | ✅ KEEP | Working offline STT |
| pyttsx3/SAPI | ✅ KEEP | Working offline TTS |
