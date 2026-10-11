"""Real local SQL gates and worker publishing; model/Auth responses are doubles."""

from types import SimpleNamespace
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from test_local_workflows import local_rpc as rpc_fixture

import app.workers.runner as worker
from app.agents.chat.context import load_context
from app.core.auth import User
from app.core.budget import Budget
from app.discovery.engine import Classification, Discovery
from app.domain.models import Claim, Draft, PostFromResearch, Report, Review, Source, Verification
from app.observability.tracing import Trace
from app.services.autonomous import AutonomousPublisher, record_checks
from app.services.jobs import Jobs
from app.services.publishing import Publishing
from app.workers.context import make_context
from app.workers.outbox import deliver_outbox
from app.workers.scheduling import schedule_research

local_rpc = rpc_fixture


@pytest.fixture
async def story(repo, local_rpc):
    settings, rpc, http, users, _ = local_rpc
    owner = users[0]
    settings.publisher_user_id = owner
    settings.publisher_access_token = type(settings.publisher_access_token)(str(owner))
    settings.autonomous_publishing_enabled = True
    await repo.execute(
        "insert into agent_private.publisher_accounts(user_id,enabled,min_interval_seconds,daily_cost_limit) values(%s,true,0,100)",
        (owner,),
    )
    for host, group, tier in [
        ("primary.example", "primary", "primary"),
        ("press.example", "press", "independent"),
    ]:
        await repo.execute(
            "insert into agent_private.publisher_sources values(%s,%s,%s,true) on conflict(host) do update set publisher_group=excluded.publisher_group,tier=excluded.tier,enabled=true",
            (host, group, tier),
        )
    topic = await repo.one("select id from topics where slug='programming'")
    cluster = await repo.one(
        "insert into story_clusters(fingerprint,summary,topic_ids) values(%s,'Verified programming release',%s) returning id",
        (str(uuid4()), [topic["id"]]),
    )
    event = str(uuid4())[:8]
    await repo.execute(
        "insert into discovered_items(canonical_url,title,published_at,content_hash,cluster_id) values(%s,'Release',now(),%s,%s)",
        (f"https://primary.example/{event}", event, cluster["id"]),
    )
    passage = f"Release {event}: the programming framework now supports persistent workflows with signed artifacts."
    sources = [
        Source(
            id="S1", url="https://primary.example/release", title="Primary release", text=passage
        ),
        Source(
            id="S2", url="https://press.example/release", title="Independent report", text=passage
        ),
    ]
    claims = [
        Claim(text=f"Release {event} supports persistent workflows", source_id=s.id, quote=passage)
        for s in sources
    ]
    report = Report(
        answer="Persistent workflows are supported [S1] [S2].",
        verified=True,
        sources=sources,
        claims=claims,
    )
    research = await repo.one(
        "insert into research_jobs(user_id,prompt,mode,payload,idempotency_key,status,report,budget) values(%s,'Research story','deep',%s,%s,'completed',%s,%s) returning *",
        (
            owner,
            Jsonb({"autonomous": True, "cluster_id": str(cluster["id"])}),
            uuid4(),
            Jsonb(report.model_dump()),
            Jsonb({"cost_reserved": 0.1}),
        ),
    )
    draft = Draft(
        title="Persistent programming workflows " + str(uuid4())[:8],
        summary="A programming release adds persistent workflows.",
        body="The programming framework now supports persistent workflows with signed artifacts [S1] [S2].",
        claims=claims,
        topic_slugs=["programming"],
    )
    response = await Jobs(repo).from_research(
        User(owner, False), PostFromResearch(research_job_id=research["id"])
    )
    job = await repo.one(
        "update research_jobs set status='running',lease_id=gen_random_uuid(),leased_until=now()+interval '5 minutes' where id=%s returning *",
        (response["job_id"],),
    )
    run = await repo.one(
        "insert into agent_runs(job_id,user_id,agent) values(%s,%s,'post') returning id",
        (job["id"], owner),
    )
    version = await Publishing(repo).save_draft(job, draft, report)
    await record_checks(repo, version["id"], run["id"], draft, report)
    yield {
        "settings": settings,
        "rpc": rpc,
        "http": http,
        "owner": owner,
        "research": research,
        "report": report,
        "draft": draft,
        "job": job,
        "version": version,
    }
    await repo.execute(
        "update research_jobs set status='cancelled' where user_id=%s and status in ('queued','running')",
        (owner,),
    )


async def publish(story):
    return await AutonomousPublisher(story["settings"], story["http"]).publish(
        story["job"], story["version"]["id"]
    )


async def test_autonomous_publication_is_atomic_idempotent_and_explorable(repo, story):
    async with repo.pool.connection() as conn, conn.transaction():
        await conn.execute("set local role service_role")
        await conn.execute(
            "select public.maxxx_command('claim',%s)",
            (Jsonb({"job_id": str(uuid4()), "lease_seconds": 360}),),
        )
    result = await publish(story)
    assert result["published"]
    assert await publish(story) == result
    post = await repo.one("select * from posts where id=%s", (story["version"]["post_id"],))
    assert post["status"] == "published"
    assert post["current_version_id"] == story["version"]["id"]
    assert (await repo.one("select count(*) n from post_reviews where post_id=%s", (post["id"],)))[
        "n"
    ] == 0
    ctx = await load_context(repo, {"user_id": story["owner"], "post_id": post["id"]})
    assert ctx["post"]["claims"] and ctx["post"]["research_report"]["verified"]
    assert ctx["post"]["version_id"] == str(post["current_version_id"])
    assert len(ctx["post"]["suggested_questions"]) == 4


@pytest.mark.parametrize(
    "gate",
    [
        "single_source",
        "same_publisher",
        "untrusted",
        "no_primary",
        "bad_quote",
        "topic",
        "expired_lease",
        "chat",
        "no_machine_check",
        "cost",
        "disabled_identity",
        "unsafe",
        "limitations",
        "stale_discovery",
    ],
)
async def test_failed_gates_never_make_a_post_public(repo, story, gate):
    job, version, research = story["job"], story["version"], story["research"]
    if gate in ("single_source", "bad_quote"):
        report = story["report"].model_dump()
        if gate == "single_source":
            report["claims"] = report["claims"][:1]
        else:
            report["claims"][1]["quote"] = "A made up quotation that is not in either page"
        await repo.execute(
            "update research_jobs set report=%s where id=%s", (Jsonb(report), research["id"])
        )
    elif gate == "same_publisher":
        await repo.execute(
            "update agent_private.publisher_sources set publisher_group='primary' where host='press.example'"
        )
    elif gate == "untrusted":
        await repo.execute(
            "update agent_private.publisher_sources set enabled=false where host='press.example'"
        )
    elif gate == "no_primary":
        await repo.execute("update agent_private.publisher_sources set tier='independent'")
    elif gate == "topic":
        await repo.execute(
            "update post_versions set topic_slugs=array['ai'] where id=%s", (version["id"],)
        )
    elif gate == "expired_lease":
        await repo.execute(
            "update research_jobs set leased_until=now()-interval '1 second' where id=%s",
            (job["id"],),
        )
    elif gate == "chat":
        await repo.execute("update research_jobs set payload='{}' where id=%s", (job["id"],))
    elif gate == "no_machine_check":
        await repo.execute(
            "delete from agent_private.publication_checks where version_id=%s", (version["id"],)
        )
    elif gate == "cost":
        await repo.execute(
            "update agent_private.publisher_accounts set daily_cost_limit=0.01 where user_id=%s",
            (story["owner"],),
        )
    elif gate == "disabled_identity":
        await repo.execute(
            "update agent_private.publisher_accounts set enabled=false where user_id=%s",
            (story["owner"],),
        )
    elif gate == "limitations":
        report = story["report"].model_dump()
        report["limitations"] = ["Conflicting source information remains unresolved"]
        await repo.execute(
            "update research_jobs set report=%s where id=%s", (Jsonb(report), research["id"])
        )
    elif gate == "stale_discovery":
        await repo.execute(
            "update discovered_items set published_at=now()-interval '10 days' where cluster_id=%s",
            (research["payload"]["cluster_id"],),
        )
    else:
        await repo.execute(
            "update post_versions set body='<script>alert(1)</script>' where id=%s",
            (version["id"],),
        )
    with pytest.raises(httpx.HTTPStatusError):
        await publish(story)
    post = await repo.one(
        "select status,current_version_id from posts where id=%s", (version["post_id"],)
    )
    assert post == {"status": "draft", "current_version_id": None}


@pytest.mark.parametrize("role", ["anon", "service_role", "authenticated"])
async def test_other_identities_cannot_call_the_publisher(repo, story, role):
    async with repo.pool.connection() as conn, conn.transaction():
        await conn.execute("select set_config('request.jwt.claim.sub',%s,true)", (str(uuid4()),))
        await conn.execute("set local role " + role)
        with pytest.raises(psycopg.Error):
            async with conn.transaction():
                await conn.execute(
                    "select public.maxxx_autopublish(%s,%s,%s)",
                    (story["job"]["id"], story["job"]["lease_id"], story["version"]["id"]),
                )


async def test_worker_publishes_without_editor_review(repo, story, monkeypatch):
    # Let the actual worker create its rendered, validated version.
    await repo.execute(
        "delete from agent_private.publication_checks where version_id=%s",
        (story["version"]["id"],),
    )
    await repo.execute("delete from post_versions where id=%s", (story["version"]["id"],))

    class Model:
        async def complete(self, messages, tier="research", schema=None):
            return (
                story["draft"]
                if schema is Draft
                else Verification(supported=[True, True], contradictions=[])
            )

    def context(*args):
        ctx = make_context(*args)
        ctx.llm = Model()
        return ctx

    monkeypatch.setattr(worker, "make_context", context)
    await worker.run_job(story["settings"], story["rpc"], story["http"], story["job"])
    final = await repo.owned_job(story["job"]["id"], story["owner"])
    assert final["status"] == "completed", final["error_code"]
    assert final["report"]["published"]


async def revision(repo, story):
    response = await Jobs(repo).from_research(
        User(story["owner"], False),
        PostFromResearch(
            research_job_id=story["research"]["id"],
            post_id=story["version"]["post_id"],
            correction_note="Verified technical clarification added.",
        ),
    )
    job = await repo.one(
        "update research_jobs set status='running',lease_id=gen_random_uuid(),leased_until=now()+interval '5 minutes' where id=%s returning *",
        (response["job_id"],),
    )
    draft = story["draft"].model_copy(
        update={"summary": "Updated: a programming release adds persistent workflows."}
    )
    version = await Publishing(repo).save_draft(job, draft, story["report"])
    run = await repo.one(
        "insert into agent_runs(job_id,user_id,agent) values(%s,%s,'post') returning id",
        (job["id"], story["owner"]),
    )
    await record_checks(repo, version["id"], run["id"], draft, story["report"])
    return {**story, "job": job, "version": version, "draft": draft}


async def test_corrections_keep_history_and_visible_note(repo, story):
    await publish(story)
    changed = await revision(repo, story)
    await publish(changed)
    ctx = (
        await load_context(
            repo, {"post_id": story["version"]["post_id"], "user_id": story["owner"]}
        )
    )["post"]
    assert len(ctx["update_history"]) == 2
    assert ctx["update_history"][0]["correction_note"] == "Verified technical clarification added."
    assert ctx["version_id"] == str(changed["version"]["id"])


async def test_post_followup_never_exposes_private_user_research(repo, story):
    await publish(story)
    report = story["report"].model_dump()
    report["answer"] = "PRIVATE_USER_CONVERSATION which was excluded from the approved article"
    await repo.execute(
        "update research_jobs set payload='{}',report=%s where id=%s",
        (Jsonb(report), story["research"]["id"]),
    )
    post = (await load_context(repo, {"user_id": uuid4(), "post_id": story["version"]["post_id"]}))[
        "post"
    ]
    assert "PRIVATE_USER_CONVERSATION" not in str(post)
    assert post["research_report"]["answer"] == story["draft"].body


@pytest.mark.parametrize(
    "gate", ["frequency", "daily_limit", "missing_note", "stale", "daily_cost"]
)
async def test_revision_gates_preserve_the_previous_public_version(repo, story, gate):
    await publish(story)
    changed = await revision(repo, story)
    if gate == "frequency":
        await repo.execute(
            "update agent_private.publisher_accounts set min_interval_seconds=900 where user_id=%s",
            (story["owner"],),
        )
    elif gate == "daily_limit":
        await repo.execute(
            "update agent_private.publisher_accounts set daily_limit=1 where user_id=%s",
            (story["owner"],),
        )
    elif gate == "daily_cost":
        await repo.execute(
            "update agent_private.publisher_accounts set daily_cost_limit=0.15 where user_id=%s",
            (story["owner"],),
        )
        await repo.execute(
            "insert into research_jobs(user_id,prompt,mode,payload,idempotency_key,status,budget) values(%s,'Failed earlier research','deep',%s,%s,'failed',%s)",
            (story["owner"], Jsonb({"autonomous": True}), uuid4(), Jsonb({"cost_reserved": 0.1})),
        )
    elif gate == "missing_note":
        await repo.execute(
            "update post_versions set correction_note=null where id=%s", (changed["version"]["id"],)
        )
    else:
        await repo.execute(
            "update post_versions set previous_version_id=null where id=%s",
            (changed["version"]["id"],),
        )
    with pytest.raises(httpx.HTTPStatusError):
        await publish(changed)
    assert (
        await repo.one(
            "select current_version_id from posts where id=%s", (story["version"]["post_id"],)
        )
    )["current_version_id"] == story["version"]["id"]


async def test_outbox_propagates_autonomous_provenance(repo, story):
    await repo.execute(
        "insert into agent_private.outbox(job_id,user_id,payload) values(%s,%s,%s)",
        (
            story["research"]["id"],
            story["owner"],
            Jsonb({"research_job_id": str(story["research"]["id"])}),
        ),
    )
    await deliver_outbox(repo)
    assert (await repo.owned_job(story["job"]["id"], story["owner"]))["payload"]["autonomous"]
    assert (
        await repo.one(
            "select status from agent_private.outbox where job_id=%s", (story["research"]["id"],)
        )
    )["status"] == "delivered"


async def test_editor_endpoint_cannot_bypass_autonomous_gates(repo, story):
    review = Review(version_id=story["version"]["id"], decision="approve")
    with pytest.raises(HTTPException) as exc:
        await Publishing(story["rpc"]).review(
            story["version"]["post_id"], User(story["owner"], True), review
        )
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await story["rpc"].command(
            "review",
            {
                "user_id": str(story["owner"]),
                "post_id": str(story["version"]["post_id"]),
                **review.model_dump(mode="json"),
            },
        )
    assert exc.value.status_code == 409


async def test_scheduler_uses_service_identity_without_editors(repo, story):
    settings = story["settings"]
    settings.editor_user_ids = []
    settings.discovery_auto_research = True
    topic = await repo.one("select id from topics where slug='programming'")
    cluster = await repo.one(
        "insert into story_clusters(fingerprint,summary,topic_ids) values(%s,'Fresh independent scheduled event',%s) returning id",
        (str(uuid4()), [topic["id"]]),
    )
    await schedule_research(settings, repo)
    queued = await repo.one(
        "select user_id,payload from research_jobs where payload->>'cluster_id'=%s",
        (str(cluster["id"]),),
    )
    assert queued["user_id"] == story["owner"]
    assert queued["payload"]["autonomous"] is True


async def test_discovery_shadow_does_not_replace_classifier_or_publish(repo, story):
    run = await repo.one(
        "insert into agent_runs(user_id,agent) values(%s,'discovery') returning id",
        (story["owner"],),
    )

    class Model:
        async def complete(self, *args, **kwargs):
            return Classification(
                topic_slugs=["programming"],
                event_key=str(uuid4()),
                existing_cluster_id=None,
                summary="A new programming release with workflow improvements",
            )

    class Gateway:
        trace = SimpleNamespace(run_id=run["id"])

        async def choose(self, *args):
            return SimpleNamespace(choice="ai", confidence=0.99)

    url = "https://primary.example/shadow/" + str(uuid4())
    assert (
        await Discovery(repo, None, None, Model(), None, Gateway()).ingest(
            [{"url": url, "title": "Programming release", "published_at": None}]
        )
        == 1
    )
    result = await repo.one(
        "select c.topic_ids from story_clusters c join discovered_items d on d.cluster_id=c.id where d.canonical_url=%s",
        (url,),
    )
    programming = await repo.one("select id from topics where slug='programming'")
    assert result["topic_ids"] == [programming["id"]]
    prediction = await repo.one(
        "select prediction from agent_private.decision_predictions where run_id=%s", (run["id"],)
    )
    assert prediction["prediction"] == "ai"


async def test_daily_budgets_are_reserved_before_external_calls(repo, story):
    await repo.execute(
        "update agent_private.publisher_accounts set daily_cost_limit=0.2,daily_search_limit=1 where user_id=%s",
        (story["owner"],),
    )
    trace = Trace(story["rpc"], None)
    trace.job = story["job"]
    budget = Budget(story["settings"], cost_reserved=0.05, searches=1)
    await trace.reserve(budget)
    budget.cost_reserved = 0.15  # Already reserved research costs 0.1.
    with pytest.raises(HTTPException):
        await trace.reserve(budget)
    saved = await repo.owned_job(story["job"]["id"], story["owner"])
    assert saved["budget"]["cost_reserved"] == 0.05
    budget.cost_reserved, budget.searches = 0.05, 2
    with pytest.raises(HTTPException):
        await trace.reserve(budget)


async def test_duplicate_claim_set_is_blocked_even_with_reworded_title(repo, story):
    await publish(story)
    topic = await repo.one("select id from topics where slug='programming'")
    cluster = await repo.one(
        "insert into story_clusters(fingerprint,summary,topic_ids) values(%s,'Same event misclustered',%s) returning id",
        (str(uuid4()), [topic["id"]]),
    )
    await repo.execute(
        "insert into discovered_items(canonical_url,title,published_at,content_hash,cluster_id) values(%s,'Duplicate',now(),'duplicate',%s)",
        ("https://primary.example/duplicate/" + str(uuid4()), cluster["id"]),
    )
    research = await repo.one(
        "insert into research_jobs(user_id,prompt,mode,payload,idempotency_key,status,report) values(%s,'Duplicate','deep',%s,%s,'completed',%s) returning id",
        (
            story["owner"],
            Jsonb({"autonomous": True, "cluster_id": str(cluster["id"])}),
            uuid4(),
            Jsonb(story["report"].model_dump()),
        ),
    )
    response = await Jobs(repo).from_research(
        User(story["owner"], False), PostFromResearch(research_job_id=research["id"])
    )
    job = await repo.one(
        "update research_jobs set status='running',lease_id=gen_random_uuid(),leased_until=now()+interval '5 minutes' where id=%s returning *",
        (response["job_id"],),
    )
    draft = story["draft"].model_copy(
        update={"title": "A differently worded headline for the same event"}
    )
    version = await Publishing(repo).save_draft(job, draft, story["report"])
    run = await repo.one(
        "insert into agent_runs(job_id,user_id,agent) values(%s,%s,'post') returning id",
        (job["id"], story["owner"]),
    )
    await record_checks(repo, version["id"], run["id"], draft, story["report"])
    with pytest.raises(httpx.HTTPStatusError):
        await publish({**story, "job": job, "version": version})
    assert (await repo.one("select status from posts where id=%s", (version["post_id"],)))[
        "status"
    ] == "draft"
