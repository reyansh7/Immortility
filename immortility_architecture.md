# Immortility System Architecture

Below is a visual flowchart of how data and execution flow through Immortility's core modules, from user input to LLM execution and filesystem edits.

```mermaid
flowchart TD
    %% Styling
    classDef user fill:#3b82f6,stroke:#2563eb,stroke-width:2px,color:white;
    classDef main fill:#8b5cf6,stroke:#7c3aed,stroke-width:2px,color:white;
    classDef router fill:#ec4899,stroke:#db2777,stroke-width:2px,color:white;
    classDef engine fill:#10b981,stroke:#059669,stroke-width:2px,color:white;
    classDef data fill:#f59e0b,stroke:#d97706,stroke-width:2px,color:white;
    classDef action fill:#ef4444,stroke:#dc2626,stroke-width:2px,color:white;
    classDef llm fill:#64748b,stroke:#475569,stroke-width:2px,color:white;

    User([User Input]):::user --> CLI["CLI Interface (main.py)"]:::main
    CLI --> ProjectDiscovery["Project Auto-Discovery\n(core/project_extract.py)"]:::main
    
    ProjectDiscovery --> Router{"Router\n(core/router.py)"}:::router
    
    %% Routes
    Router -->|"CHAT"| ChatRoute["Chat Mode"]:::engine
    Router -->|"ACTION"| ActionRoute["Action Mode"]:::action
    Router -->|"PROJECT"| ProjectRoute["Project Mode"]:::engine
    Router -->|"TASK"| TaskRoute["Workflow Mode"]:::action
    
    %% Knowledge Engine context pulling
    ProjectRoute -.-> |"Extract Context"| KE["Knowledge Engine\n(knowledge/engine.py)"]:::data
    TaskRoute -.-> |"Extract Context"| KE
    
    KE --> Chroma[("ChromaDB\n(Vector RAG)")]:::data
    KE --> Graph[("GraphEngine\n(Code Relationships)")]:::data
    
    %% Execution
    ChatRoute --> Ollama[("Ollama (qwen3:8b)")]:::llm
    
    ActionRoute --> ActionEngine["Action Engine\n(core/action_engine.py)"]:::action
    ProjectRoute -->|"Generate Plan & Confirm"| ActionEngine
    
    TaskRoute --> WF["Workflow Engine\n(core/workflow_engine.py)"]:::action
    WF --> ActionEngine
    
    %% Tool Loop
    ActionEngine -->|"JSON Tool Request"| Ollama
    Ollama -->|"Tool Call Payload"| ActionEngine
    ActionEngine -->|"Execute Action"| Tools["Tool Registry\n(read, write, terminal)"]:::action
    Tools -->|"Tool Result"| ActionEngine
    
    Tools --> FileSystem[("Local Filesystem")]:::data
    
    %% Verification Gate & Fallback
    ActionEngine -->|"Calls DONE"| Verifier{"Verification Gate\n(Mechanical + Semantic)"}:::engine
    Verifier -->|"Fails (Self-Correct)"| ActionEngine
    Verifier -->|"Fails 2+ Times"| Fallback["Fallback to Stronger Model\n(e.g., 14b+)"]:::llm
    Fallback -.->|"Updates Model"| Ollama
    
    %% Final Response
    Verifier -->|"Passes"| Response([Output to User]):::user
    ChatRoute --> Response
```

> [!NOTE] 
> The **Action Engine** forms an autonomous loop with the local LLM. It repeatedly calls tools from the **Tool Registry** (like reading files or running commands). Before completing, it must pass a **Verification Gate** (mechanical checks + semantic review). If it fails repeatedly, it can automatically fallback to a stronger model to self-correct.
