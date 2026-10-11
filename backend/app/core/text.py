"""Byte-bounded text excerpts that preserve Unicode and mark omitted content."""


def byte_excerpt(text: str, limit: int) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    marker = " [excerpt truncated]"
    return text.encode("utf-8")[: limit - len(marker)].decode("utf-8", errors="ignore") + marker
