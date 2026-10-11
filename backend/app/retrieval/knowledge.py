import json
import re
import time

from app.core.errors import ConfigurationError
from app.core.text import byte_excerpt
from app.domain.models import Source


def chunks(text: str, size=1600, overlap=200) -> list[str]:
    if size <= overlap or overlap < 0:
        raise ValueError("Chunk size must exceed overlap")
    return [
        text[i : i + size]
        for i in range(0, len(text), size - overlap)
        if text[i : i + size].strip()
    ]


class Knowledge:
    def __init__(self, repo, settings, http, budget, trace):
        self.repo, self.settings, self.http = repo, settings, http
        self.budget, self.trace = budget, trace

    async def recall(self, user_id, prompt, before):
        words = re.findall(r"\w{3,}", prompt.casefold())
        stop = {
            "what",
            "did",
            "discuss",
            "about",
            "yesterday",
            "our",
            "chat",
            "conversation",
            "previous",
            "earlier",
            "remember",
            "said",
            "the",
            "that",
            "with",
            "have",
        }
        query = " OR ".join(word for word in words if word not in stop)[:500]
        async with self.trace.tool("search_conversation"):
            rows = await self.repo.many(
                """
                select role,content,created_at,conversation_id from messages where user_id=%s
                and created_at<=%s and not(role='user' and content=%s)
                and (%s='' or to_tsvector('english',content) @@ websearch_to_tsquery('english',%s))
                order by created_at desc limit 20
            """,
                (user_id, before, prompt, query, query),
            )
        recalled = [
            {
                **r,
                "content": byte_excerpt(r["content"], 1200),
                "created_at": str(r["created_at"]),
                "conversation_id": str(r["conversation_id"]),
            }
            for r in rows
        ]
        bounded, used = [], 2
        for row in recalled:
            size = len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + 2
            if used + size > self.settings.chat_history_max_bytes:
                break
            bounded.append(row)
            used += size
        return bounded

    async def embedding(self, text: str):
        if self.settings.embedding_dimensions != 1536:
            raise ConfigurationError(
                "Migration uses 1536-dimensional embeddings; change schema first"
            )
        before = self.budget.cost_reserved
        self.budget.reserve_model(text, 1)
        await self.trace.reserve(self.budget)
        start = time.monotonic()
        async with self.trace.tool("embedding"):
            response = await self.http.post(
                "https://openrouter.ai/api/v1/embeddings",
                headers={
                    "Authorization": "Bearer " + self.settings.openrouter_api_key.get_secret_value()
                },
                json={
                    "model": self.settings.model_embedding,
                    "input": text,
                    "dimensions": self.settings.embedding_dimensions,
                },
            )
            response.raise_for_status()
            data = response.json()
            vector = data["data"][0]["embedding"]
            if len(vector) != 1536:
                raise ValueError("Unexpected embedding dimensions")
            await self.trace.model(
                self.settings.model_embedding,
                data.get("usage", {}),
                int((time.monotonic() - start) * 1000),
                self.budget.cost_reserved - before,
                "completed",
            )
            return vector

    async def search(self, user_id, query: str) -> list[Source]:
        async with self.trace.tool("search_knowledge"):
            rows = await self.repo.many(
                """
                select source_url,title,content from knowledge_chunks
                where user_id=%s and search_vector @@ websearch_to_tsquery('english',%s)
                order by ts_rank(search_vector,websearch_to_tsquery('english',%s)) desc limit 6
            """,
                (user_id, query, query),
            )
            if self.settings.model_embedding:
                vector = json.dumps(await self.embedding(query))
                semantic = await self.repo.many(
                    """
                    select source_url,title,content from knowledge_chunks where user_id=%s
                    and embedding is not null
                    order by embedding OPERATOR(extensions.<=>) %s::extensions.vector limit 6
                """,
                    (user_id, vector),
                )
                seen = {r["content"] for r in rows}
                rows += [r for r in semantic if r["content"] not in seen]
            return [
                Source(id=f"K{i + 1}", url=r["source_url"], title=r["title"], text=r["content"])
                for i, r in enumerate(rows[:8])
            ]

    async def save(self, user_id, job_id, sources: list[Source]):
        # Reusable verified research only. Never embed every search result.
        for source in sources:
            for text in chunks(source.text[:6000])[:4]:
                await self.repo.execute(
                    """
                    insert into knowledge_chunks(user_id,job_id,source_url,title,content)
                    values(%s,%s,%s,%s,%s) on conflict do nothing
                """,
                    (user_id, job_id, source.url, source.title, text),
                )

    async def index_pending(self, user_id, limit=10):
        rows = await self.repo.many(
            """
            select id,content from knowledge_chunks where user_id=%s and embedding is null
            order by created_at limit %s
        """,
            (user_id, limit),
        )
        for row in rows:
            vector = json.dumps(await self.embedding(row["content"]))
            await self.repo.execute(
                "update knowledge_chunks set embedding=%s::extensions.vector where id=%s and user_id=%s",
                (vector, row["id"], user_id),
            )
