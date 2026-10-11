"""Job-scoped reading of captured public documents; no browser or publishing authority."""

import asyncio
import re

from app.core.errors import FetchRejected
from app.domain.models import Page, Passage
from app.research.content import boilerplate_only
from app.research.selection import document_date
from app.tools.extractor import extract_async
from app.tools.fetcher import canonical_url


def index_passages(source_id: str, text: str) -> list[Passage]:
    """Character offsets address the exact bounded extraction, never an LLM offset."""
    passages = []
    for match in re.finditer(r"\S[\s\S]*?(?=(?<=[.!?])\s+|\n+|$)", text):
        start, end = match.span()
        while end > start and text[end - 1].isspace():
            end -= 1
        for offset in range(start, end, 700):
            stop = min(offset + 700, end)
            if stop - offset < 20:
                continue
            passages.append(
                Passage(
                    source_id=source_id,
                    passage_id=f"{source_id}-P{len(passages) + 1}",
                    text=text[offset:stop],
                    start_offset=offset,
                    end_offset=stop,
                )
            )
    return passages


def relevant_passages(passages, query: str, limit: int = 3):
    words = set(re.findall(r"\w{3,}", query.casefold())) - {
        "the",
        "this",
        "that",
        "what",
        "which",
        "search",
        "web",
        "week",
        "latest",
        "new",
    }
    ranked = sorted(
        passages,
        key=lambda p: (
            -len(words & set(re.findall(r"\w{3,}", p.text.casefold()))),
            p.start_offset,
        ),
    )
    return [p for p in ranked if words & set(re.findall(r"\w{3,}", p.text.casefold()))][:limit]


class PageReader:
    def __init__(self, ctx, pages=(), aliases=None):
        self.ctx = ctx
        self.pages = {p.source_id: p for p in (Page.model_validate(p) for p in pages)}
        self.aliases = dict(aliases or {})
        for page in self.pages.values():
            self.aliases[page.canonical_url] = page.source_id

    async def web_search(self, query: str) -> list[dict]:
        """Discovery only: snippets and search dates are never source passages."""
        results = await self.ctx.search.search(query[:500])
        normalized = []
        for result in results[:10]:
            try:
                url = canonical_url(result["url"])
            except (FetchRejected, ValueError):
                continue
            normalized.append(
                {
                    "url": url,
                    "canonical_url": url,
                    "title": result.get("title", "")[:300],
                    "snippet": result.get("snippet", "")[:1000],
                    "search_date": result.get("search_date", result.get("published_at")),
                    "kind": "search_candidate",
                }
            )
        return normalized

    async def open_page(self, url_or_source_id: str, *, title="", search_date=None) -> Page:
        self.ctx.budget.check()
        if url_or_source_id in self.pages:
            return self.pages[url_or_source_id]
        url = canonical_url(url_or_source_id)
        if url in self.aliases:
            return self.pages[self.aliases[url]]
        self.ctx.budget.take_page()
        await self.ctx.trace.reserve(self.ctx.budget)
        await self.ctx.event("reading", {"url": url})  # lease / cancellation fence
        async with self.ctx.trace.tool("fetch_url"):
            final, status, headers, data = await self.ctx.fetcher.fetch(url)
        final = canonical_url(final)
        if final in self.aliases:
            self.aliases[url] = self.aliases[final]
            return self.pages[self.aliases[final]]
        async with self.ctx.trace.tool("extract_content"):
            text = (
                await asyncio.wait_for(extract_async(data, headers.get("content-type", "")), 20)
            )[:6000]
        if boilerplate_only(text):
            raise FetchRejected("Navigation/footer-only extraction")
        published = document_date(data, text)
        source_id = f"S{max((int(i[1:]) for i in self.pages), default=0) + 1}"
        page = Page(
            source_id=source_id,
            url=final,
            canonical_url=final,
            title=title[:300] or final,
            text=text,
            published_at=published.isoformat() if published else None,
            date_confidence="document" if published else "unknown",
            search_date=search_date,
            content_type=headers.get("content-type", "")[:100],
            status=status,
            truncated=len(text) == 6000,
            passages=index_passages(source_id, text),
        )
        self.pages[source_id] = page
        self.aliases[url] = self.aliases[final] = source_id
        return page

    def find_in_page(self, source_id: str, query: str) -> list[Passage]:
        self.ctx.budget.check()
        return relevant_passages(self.pages[source_id].passages, query[:500])

    def read_more(self, source_id: str, passage_id: str) -> list[Passage]:
        self.ctx.budget.check()
        passages = self.pages[source_id].passages
        index = next((i for i, p in enumerate(passages) if p.passage_id == passage_id), None)
        if index is None:
            raise FetchRejected("Unknown passage reference")
        return passages[max(0, index - 1) : index + 2]

    def snapshot(self):
        return {
            "reader_pages": [p.model_dump() for p in self.pages.values()],
            "reader_aliases": self.aliases,
        }
