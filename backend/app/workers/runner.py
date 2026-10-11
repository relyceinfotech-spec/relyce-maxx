import asyncio
import json
import logging
import os
import time
from uuid import UUID, uuid4

import httpx

from app.agents.chat.graph import build_graph
from app.agents.post.graph import build_post_graph
from app.core.config import Settings
from app.core.errors import ConfigurationError, LeaseLost
from app.core.runtime import event_loop
from app.database.checkpoints import checkpointer
from app.database.runtime import database
from app.discovery.engine import Discovery
from app.domain.models import ChatReport, Draft, PostFromResearch, Report, UpdateDecision
from app.services.autonomous import AutonomousPublisher, record_checks
from app.services.publishing import Publishing
from app.workers.context import make_context
from app.workers.outbox import deliver_outbox
from app.workers.scheduling import schedule_research

logger = logging.getLogger("maxxx.worker")


async def run_job(settings, repo, http, job, publisher=None):
    run = await repo.one(
        """
        insert into agent_runs(job_id,user_id,agent) values(%s,%s,%s) returning id
    """,
        (
            job["id"],
            job["user_id"],
            "post" if job["kind"] == "draft" or job["payload"].get("autonomous") else "chat",
        ),
    )
    ctx = make_context(settings, repo, http, job, run["id"])
    config = {
        "configurable": {
            "thread_id": f"{job['user_id']}:{job['id']}:{job['checkpoint_generation']}"
        },
        "recursion_limit": 40 + 4 * (settings.deep_max_pages + settings.deep_max_searches),
    }
    try:
        async with asyncio.timeout(settings.run_timeout_seconds):
            async with checkpointer(repo) as saver:
                if job["kind"] == "draft":
                    research = await repo.owned_job(
                        UUID(job["payload"]["research_job_id"]), job["user_id"]
                    )
                    if not research or research["status"] != "completed":
                        raise ConfigurationError("Research unavailable")
                    graph = build_post_graph(ctx, saver)
                    initial = {"report": research["report"]}
                else:
                    graph = build_graph(ctx, saver)
                    initial = {"prompt": job["prompt"]}
                saved = await graph.aget_state(config)
                # An explicit failed-job retry uses a new checkpoint thread; expired leases resume.
                if saved.values:
                    for key, value in saved.values.get("budget", {}).items():
                        setattr(ctx.budget, key, max(value, getattr(ctx.budget, key)))
                    ctx.budget.deep = saved.values.get("route", {}).get("mode") == "deep"
                if saved.values and not saved.next:
                    result = saved.values
                else:
                    result = await graph.ainvoke(None if saved.values else initial, config)
                if job["kind"] == "draft":
                    version = await Publishing(repo).save_draft(
                        job, Draft(**result["draft"]), Report(**research["report"])
                    )
                    report_data = {
                        "answer": "Draft ready for editor review",
                        "verified": True,
                        "post_id": str(version["post_id"]),
                        "version_id": str(version["id"]),
                    }
                    if job["payload"].get("autonomous"):
                        if result.get("validated") is not True:
                            raise ConfigurationError("Post machine validation is missing")
                        stored = await repo.one(
                            "select title,summary,body,topic_slugs,claims from post_versions where id=%s",
                            (version["id"],),
                        )
                        if Draft(**stored).model_dump() != Draft(**result["draft"]).model_dump():
                            raise ConfigurationError(
                                "Saved version differs from the validated draft"
                            )
                        await record_checks(
                            repo,
                            version["id"],
                            run["id"],
                            Draft(**result["draft"]),
                            Report(**research["report"]),
                        )
                        await ctx.event(
                            "publication_checks_passed", {"version_id": str(version["id"])}
                        )
                        await (publisher or AutonomousPublisher(settings, http)).publish(
                            job, version["id"]
                        )
                        report_data["answer"] = (
                            "Published after autonomous evidence and quality checks"
                        )
                        report_data["published"] = True
                else:
                    report = (
                        ChatReport(**result["report"])
                        if result.get("evidence_passages") is not None
                        else Report(**result["report"])
                    )
                    if report.verified:
                        await ctx.knowledge.save(job["user_id"], job["id"], report.sources)
                    report_data = report.model_dump()
                draft_request = None
                if job["payload"].get("draft_after") and report_data.get("verified"):
                    draft_request = PostFromResearch(research_job_id=job["id"])
                    if job["payload"].get("scheduled_recheck"):
                        original = await repo.one(
                            """
                            select v.title,v.summary,v.body from posts p join post_versions v
                            on v.id=p.current_version_id where p.id=%s and p.status='published'
                        """,
                            (job["post_id"],),
                        )
                        decision = await ctx.llm.complete(
                            [
                                {
                                    "role": "user",
                                    "content": "Compare the existing article with the new verified research. "
                                    "Set changed=true only for a material factual update or correction, "
                                    "not rewording. Give a brief update note.\n"
                                    + json.dumps(
                                        {"previous": original, "new": report_data["answer"]}
                                    ),
                                }
                            ],
                            tier="fast",
                            schema=UpdateDecision,
                        )
                        draft_request = (
                            PostFromResearch(
                                research_job_id=job["id"],
                                post_id=job["post_id"],
                                correction_note=decision.note,
                            )
                            if decision.changed
                            else None
                        )
                        await ctx.event("update_assessed", decision.model_dump())
                await repo.finish(
                    job,
                    report_data,
                    run["id"],
                    draft_request.model_dump(mode="json") if draft_request else None,
                )
    except LeaseLost:
        await repo.execute(
            "update agent_runs set status='cancelled',finished_at=now() where id=%s", (run["id"],)
        )
    except Exception as exc:
        # Log categories and IDs, never provider bodies, prompts, tokens or credentials.
        category = (
            f"ProviderHTTP{exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        logger.error("job_failed job_id=%s category=%s", job["id"], category)
        if job["payload"].get("autonomous"):
            try:
                await ctx.event("publication_withheld", {"category": category})
            except LeaseLost:
                pass
        await repo.fail(job, run["id"], category)


async def serve():
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    settings = Settings()
    if settings.missing():
        raise ConfigurationError("Fill backend/.env: " + ", ".join(settings.missing()))
    next_discovery = 0
    worker_id = uuid4()
    async with (
        httpx.AsyncClient(timeout=settings.http_timeout_seconds, trust_env=False) as http,
        database(settings, http) as repo,
    ):
        repo.lease_seconds = settings.job_lease_seconds
        publisher = AutonomousPublisher(settings, http)
        while True:
            await repo.execute(
                """
                insert into agent_private.worker_heartbeats(worker_id) values(%s)
                on conflict(worker_id) do update set last_seen_at=now()
            """,
                (worker_id,),
            )
            await deliver_outbox(repo)
            if (
                settings.discovery_enabled or settings.recheck_enabled
            ) and time.monotonic() >= next_discovery:
                next_discovery = time.monotonic() + settings.discovery_interval_seconds
                if not settings.publisher_user_id:
                    logger.error("discovery_requires_publisher_user_id")
                else:
                    user_id = settings.publisher_user_id
                    run = await repo.one(
                        "insert into agent_runs(user_id,agent) values(%s,'discovery') returning id",
                        (user_id,),
                    )
                    ctx = make_context(settings, repo, http, {"mode": "deep"}, run["id"])
                    try:
                        async with asyncio.timeout(settings.run_timeout_seconds):
                            if settings.discovery_enabled:
                                await Discovery(
                                    repo,
                                    ctx.fetcher,
                                    ctx.search,
                                    ctx.llm,
                                    ctx.budget,
                                    ctx.decisions,
                                ).poll()
                            await schedule_research(settings, repo)
                        await repo.execute(
                            "update agent_runs set status='completed',finished_at=now() where id=%s",
                            (run["id"],),
                        )
                    except Exception as exc:
                        await repo.execute(
                            "update agent_runs set status='failed',error_code=%s,finished_at=now() where id=%s",
                            (type(exc).__name__, run["id"]),
                        )
            job = await repo.claim()
            if job:
                await run_job(settings, repo, http, job, publisher)
            else:
                await asyncio.sleep(settings.worker_poll_seconds)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(serve(), loop_factory=event_loop)


if __name__ == "__main__":
    main()
