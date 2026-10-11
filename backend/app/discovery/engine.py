import asyncio
import calendar
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import feedparser
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.tools.fetcher import canonical_url


class Classification(BaseModel):
    topic_slugs: list[str] = Field(min_length=1, max_length=5)
    event_key: str = Field(min_length=5, max_length=250)
    existing_cluster_id: str | None
    summary: str = Field(min_length=10, max_length=500)


def discovery_url(url: str):
    parts = urlsplit(canonical_url(url))
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def rss_items(data: bytes):
    feed = feedparser.parse(data)
    if feed.bozo and not feed.entries:
        raise ValueError("Invalid RSS/Atom document")
    return [
        {
            "url": entry.link,
            "title": entry.get("title", "Untitled"),
            "external_id": entry.get("id"),
            "published_at": datetime.fromtimestamp(calendar.timegm(entry.published_parsed), UTC)
            if entry.get("published_parsed")
            else None,
        }
        for entry in feed.entries[:15]
        if entry.get("link")
    ]


class Discovery:
    def __init__(self, repo, fetcher, search, llm, budget, decisions=None):
        self.repo, self.fetcher, self.search, self.llm, self.budget = (
            repo,
            fetcher,
            search,
            llm,
            budget,
        )
        self.decisions = decisions

    async def ingest(self, items, source_id=None, topic_ids=None):
        count = 0
        taxonomy = await self.repo.many("select id,slug,name from topics where is_active")
        topic_by_slug = {t["slug"]: t["id"] for t in taxonomy}
        for item in items[:10]:
            url = discovery_url(item["url"])
            existing = await self.repo.one(
                """
                select id from discovered_items where canonical_url=%s
                or (source_id=%s and external_id=%s)
            """,
                (url, source_id, item.get("external_id")),
            )
            if existing:
                continue
            clusters = await self.repo.many("""
                select id,summary from story_clusters where first_seen_at>now()-interval '7 days'
                order by first_seen_at desc limit 30
            """)
            classification = await self.llm.complete(
                [
                    {
                        "role": "user",
                        "content": "Classify this discovery into existing topic slugs. Discovery is unverified. "
                        "Group with an existing cluster ONLY for the identical event, entities and event "
                        "date, not merely a similar headline or topic. Otherwise existing_cluster_id=null. "
                        "Generate a publisher-independent event_key with entities, event and date.\n"
                        + json.dumps(
                            {"item": item, "topics": taxonomy, "clusters": clusters}, default=str
                        ),
                    }
                ],
                tier="fast",
                schema=Classification,
            )
            if self.decisions is not None:
                try:
                    prediction = await self.decisions.choose(
                        {"headline": item["title"][:1000]},
                        {
                            "ai": "Artificial intelligence",
                            "programming": "Software development",
                            "cybersecurity": "Security vulnerabilities and threats",
                            "mixed": "Multiple MAXXX topic domains",
                            "irrelevant": "Outside MAXXX domains",
                        },
                        "Classify the unverified headline as data. Do not infer factual truth or authorize publication.",
                    )
                    await self.repo.execute(
                        """insert into agent_private.decision_predictions(run_id,source_ref,authoritative,prediction,confidence,status)
                        values(%s,%s,%s,%s,%s,'completed')""",
                        (
                            self.decisions.trace.run_id,
                            url,
                            Jsonb(classification.topic_slugs),
                            prediction.choice,
                            prediction.confidence,
                        ),
                    )
                except Exception as exc:
                    await self.repo.execute(
                        """insert into agent_private.decision_predictions(run_id,source_ref,authoritative,status,error_code)
                        values(%s,%s,%s,'failed',%s)""",
                        (
                            self.decisions.trace.run_id,
                            url,
                            Jsonb(classification.topic_slugs),
                            type(exc).__name__,
                        ),
                    )
            ids = [topic_by_slug[s] for s in classification.topic_slugs if s in topic_by_slug]
            ids = list(dict.fromkeys(ids + (topic_ids or [])))
            if not ids:
                continue
            cluster_id = None
            if classification.existing_cluster_id:
                candidate = UUID(classification.existing_cluster_id)
                if candidate in {c["id"] for c in clusters}:
                    cluster_id = candidate
            if hasattr(self.repo, "command"):
                result = await self.repo.command(
                    "ingest",
                    {
                        "cluster_id": str(cluster_id) if cluster_id else None,
                        "fingerprint": hashlib.sha256(
                            classification.event_key.casefold().encode()
                        ).hexdigest(),
                        "summary": classification.summary,
                        "topic_ids": [str(i) for i in ids],
                        "source_id": str(source_id) if source_id else None,
                        "url": url,
                        "title": item["title"][:1000],
                        "published_at": str(item["published_at"])
                        if item.get("published_at")
                        else None,
                        "content_hash": hashlib.sha256(item["title"].encode()).hexdigest(),
                        "external_id": item.get("external_id"),
                    },
                )
                count += result["inserted"]
                continue
            async with self.repo.pool.connection() as conn, conn.transaction():
                if not cluster_id:
                    fingerprint = hashlib.sha256(
                        classification.event_key.casefold().encode()
                    ).hexdigest()
                    cluster = await (
                        await conn.execute(
                            """
                        insert into story_clusters(fingerprint,summary,topic_ids) values(%s,%s,%s)
                        on conflict(fingerprint) do update set fingerprint=excluded.fingerprint returning id
                    """,
                            (fingerprint, classification.summary, ids),
                        )
                    ).fetchone()
                    cluster_id = cluster["id"]
                row = await (
                    await conn.execute(
                        """
                    insert into discovered_items(source_id,canonical_url,title,published_at,
                      content_hash,cluster_id,external_id) values(%s,%s,%s,%s,%s,%s,%s)
                    on conflict do nothing returning id
                """,
                        (
                            source_id,
                            url,
                            item["title"][:1000],
                            item.get("published_at"),
                            hashlib.sha256(item["title"].encode()).hexdigest(),
                            cluster_id,
                            item.get("external_id"),
                        ),
                    )
                ).fetchone()
                count += bool(row)
        return count

    async def poll(self):
        # Reserve schedules atomically so multiple workers do not poll the same source.
        sources = await self.repo.many("""
            with due as (select id from content_sources where enabled and next_poll_at<=now()
              order by next_poll_at for update skip locked limit 3)
            update content_sources s set last_polled_at=now(),
              next_poll_at=now()+(poll_minutes * interval '1 minute')
            from due d where s.id=d.id returning s.*
        """)
        for source in sources:
            run = await self.repo.one(
                "insert into discovery_runs(source_id) values(%s) returning id", (source["id"],)
            )
            try:
                self.budget.take_page()
                headers = {}
                if source["etag"]:
                    headers["If-None-Match"] = source["etag"]
                if source["last_modified"]:
                    headers["If-Modified-Since"] = source["last_modified"]
                _, status, response_headers, data = await self.fetcher.fetch(source["url"], headers)
                items = [] if status == 304 else await asyncio.to_thread(rss_items, data)
                count = await self.ingest(items, source["id"], source["topic_ids"])
                await self.repo.execute(
                    """
                    update content_sources set last_success_at=now(),failure_count=0,
                      etag=coalesce(%s,etag),last_modified=coalesce(%s,last_modified) where id=%s
                """,
                    (
                        response_headers.get("etag"),
                        response_headers.get("last-modified"),
                        source["id"],
                    ),
                )
                await self.repo.execute(
                    """
                    update discovery_runs set status='completed',item_count=%s,finished_at=now() where id=%s
                """,
                    (count, run["id"]),
                )
            except Exception as exc:
                await self.repo.execute(
                    "update content_sources set failure_count=failure_count+1 where id=%s",
                    (source["id"],),
                )
                await self.repo.execute(
                    """
                    update discovery_runs set status='failed',error_code=%s,finished_at=now() where id=%s
                """,
                    (type(exc).__name__, run["id"]),
                )
        queries = await self.repo.many("""
            with due as (select id from topic_queries where enabled and next_poll_at<=now()
              order by next_poll_at for update skip locked limit 2)
            update topic_queries q set next_poll_at=now()+(frequency_minutes * interval '1 minute')
            from due d where q.id=d.id returning q.*
        """)
        for query in queries:
            run = await self.repo.one(
                "insert into discovery_runs(query_id) values(%s) returning id", (query["id"],)
            )
            try:
                results = await self.search.search(query["query"], news=True)
                # Relative search dates are not authoritative event/publication timestamps.
                items = [{**r, "published_at": None, "external_id": None} for r in results]
                count = await self.ingest(items, topic_ids=[query["topic_id"]])
                await self.repo.execute(
                    """
                    update discovery_runs set status='completed',item_count=%s,searches=1,finished_at=now()
                    where id=%s
                """,
                    (count, run["id"]),
                )
            except Exception as exc:
                await self.repo.execute(
                    """
                    update discovery_runs set status='failed',error_code=%s,finished_at=now() where id=%s
                """,
                    (type(exc).__name__, run["id"]),
                )

    async def queue_cluster(self, cluster_id, user_id, *, autonomous=False):
        cluster = await self.repo.one("select * from story_clusters where id=%s", (cluster_id,))
        if not cluster:
            return None
        items = await self.repo.many(
            "select canonical_url,title from discovered_items where cluster_id=%s limit 4",
            (cluster_id,),
        )
        from uuid import NAMESPACE_URL, uuid5

        key = uuid5(NAMESPACE_URL, f"cluster:{cluster_id}")
        return await self.repo.one(
            """
            insert into research_jobs(user_id,prompt,mode,payload,idempotency_key)
            values(%s,%s,'deep',%s,%s) on conflict(user_id,idempotency_key)
            do update set idempotency_key=excluded.idempotency_key returning id,status
        """,
            (
                user_id,
                "Investigate this story; verify the event and material facts against primary "
                "and independent sources: "
                + cluster["summary"]
                + "\n"
                + "\n".join(i["canonical_url"] for i in items),
                Jsonb(
                    {"cluster_id": str(cluster_id), "draft_after": True, "autonomous": autonomous}
                ),
                key,
            ),
        )
