"""Offline API/worker acceptance using real SQL migrations and RPC functions.

Only Auth, model, search and page responses are doubles. PostgreSQL, permissions,
RPC binding, checkpoints, jobs, knowledge, publication and feed queries are real.
"""

import json
from typing import TypedDict
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from langgraph.graph import END, START, StateGraph
from psycopg.types.json import Jsonb

import app.workers.runner as worker
from app.core.config import Settings
from app.core.errors import LeaseLost
from app.database.checkpoints import SupabaseSaver
from app.database.supabase import SupabaseRepository, decode_row
from app.domain.models import (
    ChatReport,
    ChatRequest,
    ChatVerification,
    Claim,
    Draft,
    PassageClaim,
    Plan,
    Report,
    Verification,
)
from app.main import create_app
from app.services.jobs import Jobs
from app.workers.context import make_context


@pytest.fixture
async def local_rpc(repo):
    users = [uuid4(), uuid4(), uuid4()]
    for user in users:
        await repo.execute("insert into auth.users(id) values(%s)", (user,))
    settings = Settings(
        _env_file=None,
        supabase_url="https://offline.invalid",
        supabase_publishable_key="offline-public",
        supabase_secret_key="offline-server",
        openrouter_api_key="offline-model",
        serper_api_key="offline-search",
        model_chat="offline",
        model_fast="offline",
        model_research="offline",
        editor_user_ids=[users[2]],
        api_requests_per_minute=100,
    )
    calls = []

    async def handler(request):
        assert request.url.host == "offline.invalid", "External network is forbidden"
        if request.url.path == "/auth/v1/user":
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            return (
                httpx.Response(200, json={"id": token})
                if token in map(str, users)
                else httpx.Response(401)
            )
        payload = json.loads(request.content)
        if request.url.path == "/rest/v1/rpc/maxxx_autopublish":
            assert request.headers["apikey"] == "offline-public"
            token = request.headers["Authorization"].removeprefix("Bearer ")
            if token not in map(str, users):
                return httpx.Response(401)
            try:
                async with repo.pool.connection() as conn, conn.transaction():
                    await conn.execute("set local role authenticated")
                    await conn.execute(
                        "select set_config('request.jwt.claim.sub',%s,true)", (token,)
                    )
                    row = await (
                        await conn.execute(
                            "select public.maxxx_autopublish(%s,%s,%s) result",
                            (payload["p_job_id"], payload["p_lease_id"], payload["p_version_id"]),
                        )
                    ).fetchone()
                return httpx.Response(200, json=row["result"])
            except psycopg.Error as exc:
                if not exc.sqlstate.startswith("PT"):
                    raise AssertionError(f"Local publication SQL failed: {exc}") from exc
                return httpx.Response(int(exc.sqlstate[2:]), json={"code": exc.sqlstate})
        assert request.headers["apikey"] == "offline-server"
        operations = {
            "/rest/v1/rpc/maxxx_query": (
                "select public.maxxx_query(%s,%s) result",
                (payload.get("operation_id"), Jsonb(payload.get("parameters"))),
            ),
            "/rest/v1/rpc/maxxx_command": (
                "select public.maxxx_command(%s,%s) result",
                (payload.get("operation"), Jsonb(payload.get("payload"))),
            ),
            "/rest/v1/rpc/maxxx_checkpoint": (
                "select public.maxxx_checkpoint(%s,%s) result",
                (payload.get("operation"), Jsonb(payload.get("payload"))),
            ),
        }
        sql, params = operations[request.url.path]
        calls.append(request.url.path)
        try:
            async with repo.pool.connection() as conn, conn.transaction():
                await conn.execute("set local role service_role")
                row = await (await conn.execute(sql, params)).fetchone()
            # json=None in httpx means "no body"; RPC SQL NULL is JSON null.
            return httpx.Response(
                200, content=json.dumps(row["result"]), headers={"content-type": "application/json"}
            )
        except psycopg.Error as exc:
            code = exc.sqlstate or ""
            status = int(code[2:]) if code.startswith("PT") else 409 if code == "23505" else 500
            return httpx.Response(status, json={"code": code})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        yield settings, SupabaseRepository(settings, http), http, users, calls


class Model:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, tier="research", schema=None):
        self.calls.append((messages, schema))
        if schema is None:
            return "An async function returns a coroutine that you can await."
        if schema is Plan:
            return Plan(queries=["Python asyncio concurrent code"])
        content = messages[-1]["content"]
        data = json.loads(content[content.index("\n{") + 1 :])
        if schema in (Report, ChatReport):
            source = data["evidence"][0]
            passage = source["passages"][0] if schema is ChatReport else None
            quote = passage["text"][:300] if passage else source["text"][:300]
            claim = (
                PassageClaim(
                    text=quote,
                    source_id=source["id"],
                    passage_id=passage["passage_id"],
                    quote=quote,
                )
                if passage
                else Claim(text=quote, source_id=source["id"], quote=quote)
            )
            return schema(
                answer=quote + " [" + source["id"] + "].",
                claims=[claim],
                sentence_claims=[{"sentence_index": 0, "claim_indices": [0]}],
            )
        if schema is Draft:
            claim = Claim(**data["report"]["claims"][0])
            return Draft(
                title="Python asyncio",
                summary="Asyncio runs concurrent code.",
                body=claim.quote + " [" + claim.source_id + "].",
                topic_slugs=["programming"],
                claims=[claim],
            )
        if schema in (Verification, ChatVerification):
            return schema(
                supported=[True for _ in data["claims"]],
                contradictions=[],
                **(
                    {"answer_coverage_supported": True, "coverage_failures": []}
                    if schema is ChatVerification
                    else {}
                ),
            )
        raise AssertionError("Unexpected model request")


@pytest.fixture
def offline_tools(monkeypatch):
    model = Model()

    class Search:
        async def search(self, query):
            return []

    class Page:
        async def fetch(self, url):
            text = (
                "Asyncio is a library to write concurrent code using the async await syntax. " * 15
            )
            return (
                url,
                200,
                {"content-type": "text/html"},
                (
                    "<html><body><article><h1>Asyncio</h1><p>"
                    + text
                    + "</p></article></body></html>"
                ).encode(),
            )

    def context(*args):
        ctx = make_context(*args)
        ctx.llm, ctx.search, ctx.fetcher = model, Search(), Page()
        return ctx

    monkeypatch.setattr(worker, "make_context", context)
    return model


async def run_claimed(settings, rpc, http, job_id):
    job = await rpc.command("claim", {"job_id": str(job_id), "lease_seconds": 360})
    assert job is not None
    await worker.run_job(settings, rpc, http, decode_row(job))
    result = await rpc.owned_job(UUID(str(job_id)), UUID(job["user_id"]))
    assert result["status"] == "completed", result["error_code"]
    return result


async def test_chat_research_draft_approval_and_feed(local_rpc, offline_tools):
    settings, rpc, http, users, calls = local_rpc
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.repo, app.state.http = rpc, http
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as api:
            owner = {"Authorization": "Bearer " + str(users[0])}
            other = {"Authorization": "Bearer " + str(users[1])}
            editor = {"Authorization": "Bearer " + str(users[2])}
            assert (await api.get("/ready")).status_code == 200
            assert len((await api.get("/api/topics")).json()["items"]) == 17
            assert (await api.post("/api/chat", json={"prompt": "hello"})).status_code == 401
            body = {
                "prompt": "Explain async functions",
                "mode": "normal",
                "idempotency_key": str(uuid4()),
            }
            submitted = await api.post("/api/chat", headers=owner, json=body)
            assert submitted.status_code == 202
            normal = submitted.json()
            assert (await api.post("/api/chat", headers=owner, json=body)).json()[
                "job_id"
            ] == normal["job_id"]
            assert (
                await api.post("/api/chat", headers=owner, json={**body, "prompt": "Different"})
            ).status_code == 409
            assert (
                await api.get("/api/research/" + normal["job_id"], headers=other)
            ).status_code == 404
            await run_claimed(settings, rpc, http, normal["job_id"])
            followup = await api.post(
                "/api/chat",
                headers=owner,
                json={
                    "prompt": "Explain that again",
                    "mode": "normal",
                    "conversation_id": normal["conversation_id"],
                    "idempotency_key": str(uuid4()),
                },
            )
            assert followup.status_code == 202
            await run_claimed(settings, rpc, http, followup.json()["job_id"])
            assert any(m["content"] == body["prompt"] for m in offline_tools.calls[-1][0])
            messages = await api.get(
                f"/api/conversations/{normal['conversation_id']}/messages", headers=owner
            )
            assert [m["role"] for m in messages.json()["items"]] == [
                "user",
                "assistant",
                "user",
                "assistant",
            ]
            web = await api.post(
                "/api/chat",
                headers=owner,
                json={
                    "prompt": "What is asyncio? https://docs.python.org/3/library/asyncio.html",
                    "mode": "web",
                    "idempotency_key": str(uuid4()),
                },
            )
            assert web.status_code == 202
            research_id = web.json()["job_id"]
            researched = await run_claimed(settings, rpc, http, research_id)
            assert researched["report"]["verified"]
            drafted = await api.post(
                "/api/posts/from-research", headers=owner, json={"research_job_id": research_id}
            )
            assert drafted.status_code == 202
            draft = await run_claimed(settings, rpc, http, drafted.json()["job_id"])
            post_id, version_id = draft["report"]["post_id"], draft["report"]["version_id"]
            assert (await api.get("/api/posts/" + post_id)).status_code == 404
            review = {"version_id": version_id, "decision": "approve"}
            assert (
                await api.post(f"/api/posts/{post_id}/approve", headers=owner, json=review)
            ).status_code == 403
            for _ in range(2):
                approved = await api.post(
                    f"/api/posts/{post_id}/approve", headers=editor, json=review
                )
                assert approved.status_code == 200, approved.text
            article = await api.get("/api/posts/" + post_id)
            assert article.status_code == 200
            assert "[S1](https://docs.python.org/3/library/asyncio.html)" in article.json()["body"]
            feed = await api.get("/api/feed?mode=latest", headers=owner)
            assert feed.status_code == 200
            assert any(str(p["id"]) == post_id for p in feed.json()["items"])
            post_chat = await api.post(
                "/api/chat",
                headers=owner,
                json={
                    "prompt": "Explain this article",
                    "post_id": post_id,
                    "mode": "knowledge",
                    "idempotency_key": str(uuid4()),
                },
            )
            assert post_chat.status_code == 202
            grounded = await run_claimed(settings, rpc, http, post_chat.json()["job_id"])
            assert grounded["report"]["verified"]
            assert any(s["id"].startswith("P") for s in grounded["report"]["sources"])
            assert set(calls) == {
                "/rest/v1/rpc/maxxx_query",
                "/rest/v1/rpc/maxxx_command",
                "/rest/v1/rpc/maxxx_checkpoint",
            }


async def test_rpc_binding_rejects_unknown_sql_and_preserves_text(local_rpc):
    _, rpc, _, users, _ = local_rpc
    needle = "'); drop table auth.users; --"
    request = ChatRequest(prompt=needle, mode="normal", idempotency_key=uuid4())
    submitted = await Jobs(rpc).submit_chat(users[0], request)
    assert (await rpc.owned_job(submitted["job_id"], users[0]))["prompt"] == needle
    with pytest.raises(HTTPException) as error:
        await rpc.many("drop table auth.users")
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        await rpc.rpc("maxxx_query", {"operation_id": "unknown", "parameters": []})
    assert error.value.status_code == 422


async def test_atomic_rpc_rolls_back_invalid_submission_and_interests(local_rpc, repo):
    _, rpc, _, users, _ = local_rpc
    before = await repo.one("select count(*) n from conversations where user_id=%s", (users[0],))
    with pytest.raises(HTTPException) as error:
        await Jobs(rpc).submit_chat(
            users[0], ChatRequest(prompt="invalid post", post_id=uuid4(), idempotency_key=uuid4())
        )
    assert error.value.status_code == 404
    assert (
        await repo.one("select count(*) n from conversations where user_id=%s", (users[0],))
        == before
    )
    topic = await repo.one("select id from topics where slug='programming'")
    payload = {
        "user_id": str(users[0]),
        "interests": [{"topic_id": str(topic["id"]), "weight": 0.8, "blocked": False}],
    }
    assert await rpc.command("set_interests", payload) == {"saved": 1}
    with pytest.raises(HTTPException) as error:
        await rpc.command(
            "set_interests",
            {**payload, "interests": [{"topic_id": str(uuid4()), "weight": 1, "blocked": False}]},
        )
    assert error.value.status_code == 422
    remaining = await repo.one(
        "select topic_id,weight from user_interests where user_id=%s", (users[0],)
    )
    assert remaining["topic_id"] == topic["id"] and float(remaining["weight"]) == 0.8


async def test_rpc_cancelled_lease_cannot_finish(local_rpc, repo):
    _, rpc, _, users, _ = local_rpc
    result = await Jobs(rpc).submit_chat(
        users[0], ChatRequest(prompt="Cancel", idempotency_key=uuid4())
    )
    job = decode_row(
        await rpc.command("claim", {"job_id": str(result["job_id"]), "lease_seconds": 360})
    )
    run = await repo.one(
        "insert into agent_runs(user_id,job_id,agent) values(%s,%s,'chat') returning id",
        (users[0], job["id"]),
    )
    await Jobs(rpc).cancel(job["id"], users[0])
    with pytest.raises(LeaseLost):
        await rpc.finish(job, {"answer": "Do not store"}, run["id"])
    assert (await rpc.owned_job(job["id"], users[0]))["status"] == "cancelled"


async def test_checkpoint_restart_pending_writes_filters_and_delete(local_rpc):
    _, rpc, _, _, _ = local_rpc

    class State(TypedDict):
        count: int

    graph = StateGraph(State)
    graph.add_node("first", lambda state: {"count": state["count"] + 1})
    graph.add_node("second", lambda state: {"count": state["count"] + 1})
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)
    config = {"configurable": {"thread_id": "local-" + uuid4().hex}}
    saver = SupabaseSaver(rpc)
    interrupted = graph.compile(checkpointer=saver, interrupt_before=["second"])
    assert (await interrupted.ainvoke({"count": 0}, config))["count"] == 1
    restarted = graph.compile(checkpointer=SupabaseSaver(rpc))
    assert (await restarted.ainvoke(None, config))["count"] == 2
    latest = await saver.aget_tuple(config)
    await saver.aput_writes(latest.config, [("count", 3)], "task")
    await saver.aput_writes(latest.config, [("count", 99)], "task")
    writes = (await saver.aget_tuple(config)).pending_writes
    assert ("task", "count", 3) in writes
    assert ("task", "count", 99) not in writes
    history = [item async for item in saver.alist(config)]
    assert len(history) >= 4
    assert not [item async for item in saver.alist(config, filter={"unknown": "absent"})]
    assert len([item async for item in saver.alist(config, limit=1)]) == 1
    await saver.adelete_thread(config["configurable"]["thread_id"])
    assert await saver.aget_tuple(config) is None


async def test_rpc_privileges_protect_private_operations(repo):
    for role in ("anon", "authenticated"):
        async with repo.pool.connection() as conn, conn.transaction():
            await conn.execute("set local role " + role)
            for sql in (
                "select public.maxxx_command('claim','{}')",
                "select * from agent_private.operation_catalog",
                "select public.maxxx_checkpoint('list','{}')",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():
                        await conn.execute(sql)


async def test_real_pgvector_index_and_retrieval_keep_owners_isolated(local_rpc, repo, monkeypatch):
    from app.core.budget import Budget
    from app.observability.tracing import Trace
    from app.retrieval.knowledge import Knowledge

    settings, rpc, http, users, _ = local_rpc
    for user, content in (
        (users[0], "Coroutines await concurrent tasks."),
        (users[1], "Private competitor financial report."),
    ):
        await repo.execute(
            "insert into knowledge_chunks(user_id,source_url,title,content) values(%s,%s,%s,%s)",
            (user, "https://example.org/" + str(user), "Owner document", content),
        )
    run = await repo.one(
        "insert into agent_runs(user_id,agent) values(%s,'chat') returning id", (users[0],)
    )
    settings.model_embedding = "offline-vector"
    knowledge = Knowledge(rpc, settings, http, Budget(settings), Trace(rpc, run["id"]))
    vector = [1.0] + [0.0] * 1535

    async def embedding(text):
        return vector

    monkeypatch.setattr(knowledge, "embedding", embedding)
    await knowledge.index_pending(users[0])
    assert (
        await repo.one(
            "select extensions.vector_dims(embedding) dimensions from knowledge_chunks where user_id=%s",
            (users[0],),
        )
    )["dimensions"] == 1536
    assert (
        await repo.one(
            "select embedding is null absent from knowledge_chunks where user_id=%s", (users[1],)
        )
    )["absent"]
    sources = await knowledge.search(users[0], "unmatched semantic query")
    assert len(sources) == 1 and sources[0].text == "Coroutines await concurrent tasks."


async def test_failed_provider_requires_explicit_retry_with_fresh_checkpoint(
    local_rpc, offline_tools, monkeypatch
):
    settings, rpc, http, users, _ = local_rpc
    result = await Jobs(rpc).submit_chat(
        users[0], ChatRequest(prompt="Explain async", mode="normal", idempotency_key=uuid4())
    )
    job = decode_row(
        await rpc.command("claim", {"job_id": str(result["job_id"]), "lease_seconds": 360})
    )
    complete = offline_tools.complete

    async def unavailable(*args, **kwargs):
        response = httpx.Response(
            402, request=httpx.Request("POST", "https://offline.invalid/model")
        )
        raise httpx.HTTPStatusError(
            "private provider body", request=response.request, response=response
        )

    monkeypatch.setattr(offline_tools, "complete", unavailable)
    await worker.run_job(settings, rpc, http, job)
    failed = await rpc.owned_job(job["id"], users[0])
    assert failed["status"] == "failed" and failed["error_code"] == "ProviderHTTP402"
    assert not failed["report"]
    assert await rpc.command("claim", {"job_id": str(job["id"]), "lease_seconds": 360}) is None
    monkeypatch.setattr(offline_tools, "complete", complete)
    await Jobs(rpc).retry(job["id"], users[0])
    retry = await run_claimed(settings, rpc, http, job["id"])
    assert retry["checkpoint_generation"] == failed["checkpoint_generation"] + 1
    assert retry["attempts"] == 2


async def test_deep_research_executes_and_recovers_verified_evidence(local_rpc, offline_tools):
    settings, rpc, http, users, _ = local_rpc
    result = await Jobs(rpc).submit_chat(
        users[0],
        ChatRequest(
            prompt="Research asyncio https://docs.python.org/3/library/asyncio.html",
            mode="deep",
            idempotency_key=uuid4(),
        ),
    )
    completed = await run_claimed(settings, rpc, http, result["job_id"])
    assert completed["report"]["verified"]
    assert any(schema is Plan for _, schema in offline_tools.calls)
