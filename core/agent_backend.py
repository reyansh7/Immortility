"""Stable boundary between Immortility and an execution-capable agent backend.

Immortility owns intent, memory, permissions, and presentation.  A backend owns
the closed tool loop.  This keeps a provider or harness change out of the UI and
the rest of the orchestration code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AgentRunResult:
    status: str
    output: str = ""
    run_id: str = ""
    session_id: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    error_kind: str = ""
    retry_after: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed" and bool(self.output.strip())


class AgentBackend(Protocol):
    """A real agent/harness integration, not an individual tool executor."""

    async def run(
        self,
        request: str,
        *,
        context: str = "",
        session_id: str = "",
        require_edits: bool = False,
        on_event: Any = None,
    ) -> AgentRunResult: ...
