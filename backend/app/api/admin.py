from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies import repository
from app.core.auth import User, editor_user
from app.discovery.engine import Discovery
from app.domain.models import QueryInput, SourceInput
from app.tools.fetcher import canonical_url, public_addresses

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(editor_user)])


@router.get("/agent-runs")
async def runs(limit: int = Query(50, ge=1, le=100), repo=Depends(repository)):
    return {
        "items": await repo.many(
            """
        select r.*,(select coalesce(sum(u.reported_cost),0) from model_usage u where u.run_id=r.id) reported_cost,
          (select sum(u.prompt_tokens+u.completion_tokens) from model_usage u where u.run_id=r.id) tokens
        from agent_runs r order by r.started_at desc limit %s
    """,
            (limit,),
        )
    }


@router.get("/agent-runs/{run_id}")
async def run_detail(run_id: UUID, repo=Depends(repository)):
    run = await repo.one("select * from agent_runs where id=%s", (run_id,))
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "run": run,
        "models": await repo.many(
            "select * from model_usage where run_id=%s order by id", (run_id,)
        ),
        "tools": await repo.many(
            "select * from tool_executions where run_id=%s order by id", (run_id,)
        ),
    }


@router.get("/drafts")
async def drafts(repo=Depends(repository)):
    return {
        "items": await repo.many("""
        select id,post_id,title,summary,body,claims,sources,topic_slugs,previous_version_id,correction_note,created_at
        from post_versions where status='pending' order by created_at limit 100
    """)
    }


@router.get("/sources")
async def sources(repo=Depends(repository)):
    return {"items": await repo.many("select * from content_sources order by name")}


@router.post("/sources", status_code=201)
async def add_source(body: SourceInput, repo=Depends(repository)):
    from urllib.parse import urlsplit

    url = canonical_url(str(body.url))
    parts = urlsplit(url)
    await public_addresses(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    ids = await repo.many("select id from topics where id=any(%s) and is_active", (body.topic_ids,))
    if len(ids) != len(set(body.topic_ids)):
        raise HTTPException(422, "Unknown topic references")
    return await repo.one(
        """
        insert into content_sources(name,url,topic_ids,poll_minutes,enabled,trust_tier)
        values(%s,%s,%s,%s,%s,%s) on conflict(url) do update set name=excluded.name,
          topic_ids=excluded.topic_ids,poll_minutes=excluded.poll_minutes,
          enabled=excluded.enabled,trust_tier=excluded.trust_tier returning *
    """,
        (body.name, url, body.topic_ids, body.poll_minutes, body.enabled, body.trust_tier),
    )


@router.get("/queries")
async def queries(repo=Depends(repository)):
    return {"items": await repo.many("select * from topic_queries order by query")}


@router.post("/queries", status_code=201)
async def add_query(body: QueryInput, repo=Depends(repository)):
    if not await repo.one("select id from topics where id=%s and is_active", (body.topic_id,)):
        raise HTTPException(422, "Unknown topic")
    return await repo.one(
        """
        insert into topic_queries(topic_id,query,frequency_minutes,enabled) values(%s,%s,%s,%s)
        on conflict(topic_id,query) do update set frequency_minutes=excluded.frequency_minutes,
          enabled=excluded.enabled returning *
    """,
        (body.topic_id, body.query, body.frequency_minutes, body.enabled),
    )


@router.get("/discovery")
async def discovery(repo=Depends(repository)):
    return {
        "runs": await repo.many("select * from discovery_runs order by started_at desc limit 50"),
        "clusters": await repo.many(
            "select * from story_clusters order by first_seen_at desc limit 100"
        ),
    }


@router.post("/clusters/{cluster_id}/research", status_code=202)
async def research_cluster(
    cluster_id: UUID, user: User = Depends(editor_user), repo=Depends(repository)
):
    engine = Discovery(repo, None, None, None, None)
    job = await engine.queue_cluster(cluster_id, user.id)
    if not job:
        raise HTTPException(404, "Story cluster not found")
    return job


@router.get("/operations")
async def operations(request: Request, repo=Depends(repository)):
    return {
        "queue": await repo.many("select status,count(*) from research_jobs group by status"),
        "outbox": await repo.many(
            "select status,count(*) from agent_private.outbox group by status"
        ),
        "worker": await repo.one(
            "select max(last_seen_at) last_seen_at from agent_private.worker_heartbeats"
        ),
        "discovery_enabled": request.app.state.settings.discovery_enabled,
    }
