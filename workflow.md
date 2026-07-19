# Immortility Architecture & Workflow

Immortility is a local, AI-powered coding assistant and agentic framework designed to act autonomously on your local filesystem. Below is a comprehensive breakdown of its complete workflow and architecture.

## 1. Entry Point & CLI (`main.py`)
The system starts by initializing a persistent CLI interface using `prompt_toolkit`. 
- **Session Management**: It loads existing session data, chat histories, and pending actions from `AgentState`.
- **Slash Commands**: It instantly processes built-in commands (`/open`, `/auto`, `/clear`, `/memory`, `/projects`).
- **Auto-Discovery**: If you mention a project by name (e.g., "open InventoryVerification4"), it uses `extract_open_path` to find the directory on your Desktop or Documents and automatically loads it.

## 2. The Knowledge Engine (`knowledge/engine.py`)
Whenever a project is opened, Immortility builds an understanding of your codebase before answering.
- **Vector Search (ChromaDB)**: Chunks your files and stores embeddings so the LLM can perform semantic searches across thousands of lines of code.
- **Graph Engine (`knowledge/graph_engine.py`)**: Uses `graphify` under the hood to map structural dependencies (e.g., "Function A calls Function B", "File X imports File Y"). This allows the agent to navigate the codebase deterministically.
- **Context Injection**: When you ask a question, the Knowledge Engine retrieves the most relevant snippets and injects them into the prompt (RAG).

## 3. The Router (`core/router.py`)
Every natural language prompt is passed to the Router, which uses a combination of regex fast-paths and LLM classification to assign your request to exactly ONE category:

* **`CHAT`**: General conversation, greetings, or simple questions.
* **`ACTION`**: Direct commands to execute immediately (e.g., "run the server", "summarize this webpage").
* **`PROJECT`**: Questions or edits specific to the currently opened codebase (e.g., "where is the login logic?", "refactor the navbar").
* **`TASK` / `RESEARCH_TASK`**: High-level, complex goals (e.g., "implement JWT authentication").

## 4. Execution Paths

Depending on the Router's classification, Immortility executes the appropriate engine:

### A. Chat & Project Analysis
For `CHAT` and `PROJECT` routes, the system acts conversationally. It pulls in memory and RAG context, and provides a markdown response. If you ask it to design a feature, it may generate an **Implementation Plan** and ask you to reply "yes" or "make the changes" to execute it (`handle_pending_coding_plan`).

### B. The Action Engine (`core/action_engine.py`)
For `ACTION` routes (and confirmed coding plans), control is handed to the **Action Engine**. This is an autonomous loop where the LLM can take physical actions:
1. The LLM is prompted with available tools and strict rules.
2. It responds with a JSON payload specifying a tool to run (e.g., `read_file`, `write_file`, `run_command`).
3. The `ToolRegistry` (`tools/tool_registry.py`) executes the local Python function and returns the result to the LLM.
4. The loop repeats (up to 20 times) until the LLM verifies its changes and calls the `DONE` tool.

### C. The Workflow Engine (`core/workflow_engine.py` & `editing/coding_workflow.py`)
For complex `TASK` requests, you are presented with a choice:
`1. Plan  2. Code  3. Execute  4. Full Workflow`
- If you choose **3**, it skips directly to the Action Engine.
- If you choose **4**, it runs the Phase 3 Autonomous Workflow (`WorkflowEngine`), which breaks your goal into sub-tasks, assigns agents (like the `ResearchAgent`), and systematically completes them using a state machine (`workflow_state.py`).

## 5. Model Backend
Under the hood, all LLM intelligence is powered by your local **Ollama** server, specifically relying on models like `qwen3:8b`. The system leverages `asyncio` to ensure non-blocking HTTP requests to the Ollama API, allowing background tasks and UI rendering to stay smooth.

---

### Summary of Data Flow:
`User Input` ➔ `Router` ➔ `Knowledge Engine (RAG)` ➔ `LLM / Action Engine` ➔ `Filesystem/Terminal` ➔ `User Feedback`
