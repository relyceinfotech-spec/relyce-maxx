"""Real PostgreSQL tests. Only run against a fresh disposable database named maxxx_test.

CI supplies pgvector and this URL. No .env file or production database is ever consulted.
"""

import os
from uuid import uuid4

import psycopg
import pytest

from app.core.auth import User
from app.domain.models import ChatRequest, Claim, Draft, PostFromResearch, Report, Review, Source
from app.services.jobs import Jobs
from app.services.publishing import Publishing

URL = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="TEST_DATABASE_URL not set; real database verification pending"
)


async def make_user(repo):
    user = uuid4()
    await repo.execute("insert into auth.users(id) values(%s)", (user,))
    return user


async def test_concurrent_requests_are_idempotent(repo):
    import asyncio

    owner = await make_user(repo)
    request = ChatRequest(prompt="Explain closures", idempotency_key=uuid4())
    a, b = await asyncio.gather(
        Jobs(repo).submit_chat(owner, request), Jobs(repo).submit_chat(owner, request)
    )
    assert a["job_id"] == b["job_id"]
    count = await repo.one("select count(*) n from messages where user_id=%s", (owner,))
    assert count["n"] == 1
    async with repo.pool.connection() as conn, conn.transaction():
        await conn.execute("set local role anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("select sources from post_versions")
    # Finish this fixture job so later worker tests see their own queue.
    await repo.execute("update research_jobs set status='cancelled' where id=%s", (a["job_id"],))


async def test_rls_hides_other_users_research(repo):
    owner, other = await make_user(repo), await make_user(repo)
    job = await Jobs(repo).submit_chat(
        owner, ChatRequest(prompt="Private", idempotency_key=uuid4())
    )
    async with repo.pool.connection() as conn, conn.transaction():
        await conn.execute("set local role authenticated")
        await conn.execute("select set_config('request.jwt.claim.sub',%s,true)", (str(other),))
        rows = await (
            await conn.execute("select id from research_jobs where id=%s", (job["job_id"],))
        ).fetchall()
        assert not rows
        await conn.execute("select set_config('request.jwt.claim.sub',%s,true)", (str(owner),))
        rows = await (
            await conn.execute("select id from research_jobs where id=%s", (job["job_id"],))
        ).fetchall()
        assert len(rows) == 1
    await repo.execute("update research_jobs set status='cancelled' where id=%s", (job["job_id"],))


async def test_cancelled_lease_cannot_finish(repo):
    from app.core.errors import LeaseLost

    owner = await make_user(repo)
    response = await Jobs(repo).submit_chat(
        owner, ChatRequest(prompt="Cancel", idempotency_key=uuid4())
    )
    job = await repo.claim()
    run = await repo.one(
        "insert into agent_runs(user_id,job_id,agent) values(%s,%s,'chat') returning id",
        (owner, job["id"]),
    )
    await Jobs(repo).cancel(response["job_id"], owner)
    with pytest.raises(LeaseLost):
        await repo.finish(job, {"answer": "Should not appear"}, run["id"])


async def test_draft_review_is_atomic_and_idempotent(repo):
    owner = await make_user(repo)
    response = await Jobs(repo).submit_chat(
        owner, ChatRequest(prompt="Research", idempotency_key=uuid4())
    )
    research = await repo.claim()
    source = Source(
        id="S1",
        url="https://example.org/release",
        title="Release",
        text="The release includes signed artifacts with verified build metadata.",
    )
    claim = Claim(text="Signed artifacts", source_id="S1", quote=source.text)
    report = Report(
        answer="Signed artifacts [S1].", claims=[claim], sources=[source], verified=True
    )
    run = await repo.one(
        "insert into agent_runs(user_id,job_id,agent) values(%s,%s,'chat') returning id",
        (owner, research["id"]),
    )
    await repo.finish(research, report.model_dump(), run["id"])
    await Jobs(repo).from_research(
        User(owner, True), PostFromResearch(research_job_id=response["job_id"])
    )
    draft_job = await repo.claim()
    draft = Draft(
        title="Signed release artifacts",
        summary="Verified build metadata is included.",
        body="Signed artifacts are included with verified build metadata [S1](https://example.org/release).",
        topic_slugs=["programming"],
        claims=[claim],
    )
    version = await Publishing(repo).save_draft(draft_job, draft, report)
    async with repo.pool.connection() as conn, conn.transaction():
        await conn.execute("set local role anon")
        assert not await (
            await conn.execute("select id from posts where id=%s", (version["post_id"],))
        ).fetchall()
    review = Review(version_id=version["id"], decision="approve")
    await Publishing(repo).review(version["post_id"], User(owner, True), review)
    await Publishing(repo).review(version["post_id"], User(owner, True), review)
    post = await repo.one(
        "select status,current_version_id from posts where id=%s", (version["post_id"],)
    )
    assert post["status"] == "published" and post["current_version_id"] == version["id"]
    count = await repo.one(
        "select count(*) n from post_reviews where version_id=%s", (version["id"],)
    )
    assert count["n"] == 1
