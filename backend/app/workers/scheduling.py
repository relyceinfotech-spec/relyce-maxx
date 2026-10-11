from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from app.discovery.engine import Discovery


async def schedule_research(settings, repo):
    if not settings.publisher_user_id:
        return
    owner = settings.publisher_user_id
    if settings.discovery_auto_research:
        clusters = await repo.many("""
            select c.id from story_clusters c
            where c.first_seen_at>now()-interval '3 days'
            and not exists(select 1 from posts p where p.cluster_id=c.id)
            and not exists(select 1 from research_jobs j where j.payload->>'cluster_id'=c.id::text)
            order by c.first_seen_at desc limit 1
        """)
        for cluster in clusters:
            await Discovery(repo, None, None, None, None).queue_cluster(
                cluster["id"], owner, autonomous=True
            )
    if settings.recheck_enabled:
        posts = await repo.many(
            """
            select p.id,v.title from posts p join post_versions v on v.id=p.current_version_id
            where p.status='published' and p.created_by=%s
            and not exists(select 1 from post_versions pv where pv.post_id=p.id and pv.status='pending')
            and not exists(select 1 from research_jobs j where j.post_id=p.id and (
              j.status in ('queued','running') or j.created_at>now()-(%s * interval '1 hour')))
            order by p.published_at desc limit 1
        """,
            (
                owner,
                settings.recheck_min_age_hours,
            ),
        )
        for post in posts:
            key = uuid5(NAMESPACE_URL, f"recheck:{post['id']}:{datetime.now(UTC).date()}")
            await repo.execute(
                """
                insert into research_jobs(user_id,prompt,mode,post_id,payload,idempotency_key)
                values(%s,%s,'deep',%s,%s,%s) on conflict(user_id,idempotency_key) do nothing
            """,
                (
                    owner,
                    "Research current primary sources and independent evidence for updates or "
                    "corrections to this story: " + post["title"],
                    post["id"],
                    Jsonb({"draft_after": True, "scheduled_recheck": True, "autonomous": True}),
                    key,
                ),
            )
