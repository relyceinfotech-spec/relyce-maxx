import asyncio
import io

import trafilatura
from pypdf import PdfReader

from app.core.errors import FetchRejected


def extract(data: bytes, content_type: str) -> str:
    if "pdf" in content_type or data.startswith(b"%PDF"):
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise FetchRejected("Encrypted or oversized PDF")
        text = "\n".join(page.extract_text() or "" for page in reader.pages[:30])
    elif any(t in content_type for t in ("html", "xml", "text/plain")):
        text = trafilatura.extract(data, include_comments=False, include_tables=True) or ""
        if not text and "text/plain" in content_type:
            text = data.decode("utf-8", errors="replace")
    else:
        raise FetchRejected("Unsupported document type")
    if len(text.strip()) < 100:
        raise FetchRejected("Insufficient readable content")
    return text[:50000]


async def extract_async(data: bytes, content_type: str) -> str:
    return await asyncio.to_thread(extract, data, content_type)
