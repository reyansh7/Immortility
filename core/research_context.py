from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ResearchContext:
    query: str = ""
    urls: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    extracted_text: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ResearchContext | None":
        if not data:
            return None
        if isinstance(data, str):
            return cls(query="", extracted_text=data, summary=data[:500])
        return cls(
            query=data.get("query", ""),
            urls=data.get("urls", []),
            sources=data.get("sources", []),
            extracted_text=data.get("extracted_text", ""),
            summary=data.get("summary", ""),
        )

    def to_prompt(self) -> str:
        parts = [f"Query: {self.query}"]
        if self.sources:
            parts.append(f"Sources: {', '.join(self.sources)}")
        if self.urls:
            parts.append(f"URLs: {', '.join(self.urls)}")
        if self.summary:
            parts.append(f"Summary: {self.summary}")
        if self.extracted_text:
            parts.append(f"\nExtracted Content:\n{self.extracted_text}")
        return "\n".join(parts)
