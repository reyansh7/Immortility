"""Sanitize user text for safe UTF-8 encoding (RAG cache, logging)."""


def sanitize_text(text: str) -> str:
    """Remove surrogate code units that break UTF-8 encode on Windows."""
    if not text:
        return text
    return text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
