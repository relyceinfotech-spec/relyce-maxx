"""Bounded real-provider acceptance. Creates isolated Auth users; IDs enable cleanup.

No content is approved or published. Run only when deliberately testing live providers.
"""

import argparse
import asyncio
import json
import secrets
from pathlib import Path
from uuid import UUID, uuid4

import httpx

import app.workers.runner as worker
from app.core.config import Settings
from app.database.supabase import decode_row
from app.domain.models import Claim, Draft, Report, Verification
from app.main import create_app
from app.workers.context import make_context
from app.workers.runner import run_job

STATE = Path(__file__).resolve().parents[1] / ".local-state" / "smoke.json"


class DeterministicModel:
    """Explicit test response double; evidence still comes from the real public page."""

    async def complete(self, messages, tier="research", schema=None):
        if schema is None:
            return "An async Python function returns a coroutine. Await it to run it."
        data = json.loads(messages[-1]["content"].split("\n", 1)[1])
        if schema is Report:
            source = data["evidence"][0]
            quote = source["text"][:300]
            return Report(
                answer=quote + " [S1].", claims=[Claim(text=quote, source_id="S1", quote=quote)]
            )
        if schema is Draft:
            claim = Claim(**data["report"]["claims"][0])
            return Draft(
                title="Understanding Python asyncio",
                summary="Official Python documentation describes asyncio and concurrent code.",
                body=claim.quote + " [S1].",
                topic_slugs=["programming"],
                claims=[claim],
            )
        if schema is Verification:
            return Verification(supported=[True for _ in data["claims"]], contradictions=[])
        raise AssertionError("Unexpected model request")


def deterministic_context(*args):
    ctx = make_context(*args)
    ctx.llm = DeterministicModel()

    # Direct source URL is still fetched/extracted by the production fetcher.
    class NoSearch:
        async def search(self, query):
            return []

    ctx.search = NoSearch()
    return ctx


async def main():
    settings = Settings(
        max_pages=1,
        max_searches=1,
        max_model_calls=5,
        max_output_tokens=1600,
        run_timeout_seconds=180,
    )
    assert settings.database_transport == "supabase" and not settings.missing()
    if STATE.exists():
        raise RuntimeError("Clean up the previous smoke's recorded IDs before starting another run")
    STATE.parent.mkdir(exist_ok=True)
    ids = []
    key = settings.supabase_secret_key.get_secret_value()
    admin_headers = {"apikey": key}
    if key.startswith("eyJ"):
        admin_headers["Authorization"] = "Bearer " + key
    base = settings.supabase_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30, trust_env=False) as network:
        sessions = []
        for _ in range(2):
            email = "maxxx-smoke-" + uuid4().hex + "@example.invalid"
            password = secrets.token_urlsafe(32)
            created = await network.post(
                base + "/auth/v1/admin/users",
                headers=admin_headers,
                json={"email": email, "password": password, "email_confirm": True},
            )
            assert created.status_code in (200, 201), f"Auth creation status {created.status_code}"
            ids.append(created.json()["id"])
            STATE.write_text(json.dumps({"user_ids": ids}), encoding="utf-8")
            session = await network.post(
                base + "/auth/v1/token?grant_type=password",
                headers={"apikey": settings.supabase_publishable_key.get_secret_value()},
                json={"email": email, "password": password},
            )
            assert session.status_code == 200, f"Auth login status {session.status_code}"
            sessions.append({"Authorization": "Bearer " + session.json()["access_token"]})
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://smoke.local"
            ) as api,
        ):
            repo = app.state.repo
            body = {
                "prompt": "Explain what an async Python function is in two short sentences.",
                "mode": "normal",
                "idempotency_key": str(uuid4()),
            }
            submitted = await api.post("/api/chat", headers=sessions[0], json=body)
            assert submitted.status_code == 202, f"Chat submission status {submitted.status_code}"
            job_id = submitted.json()["job_id"]
            duplicate = await api.post("/api/chat", headers=sessions[0], json=body)
            assert duplicate.json()["job_id"] == job_id
            conflict = await api.post(
                "/api/chat", headers=sessions[0], json={**body, "prompt": "Different question"}
            )
            assert conflict.status_code == 409
            foreign = await api.get("/api/research/" + job_id, headers=sessions[1])
            assert foreign.status_code == 404
            job = await repo.command(
                "claim", {"job_id": job_id, "lease_seconds": settings.job_lease_seconds}
            )
            await run_job(settings, repo, app.state.http, decode_row(job))
            completed = await repo.owned_job(UUID(job_id), UUID(ids[0]))
            assert completed["status"] == "completed", (
                f"Normal Chat failed: {completed['error_code']}"
            )
            messages = await api.get(
                f"/api/conversations/{submitted.json()['conversation_id']}/messages",
                headers=sessions[0],
            )
            assert [m["role"] for m in messages.json()["items"]] == ["user", "assistant"]
            print(
                json.dumps(
                    {"normal_chat": "passed", "idempotency": "passed", "owner_isolation": "passed"}
                ),
                flush=True,
            )
            research_body = {
                "prompt": "Using the official Python page, what is asyncio used for? https://docs.python.org/3/library/asyncio.html",
                "mode": "web",
                "idempotency_key": str(uuid4()),
            }
            submitted = await api.post("/api/chat", headers=sessions[0], json=research_body)
            assert submitted.status_code == 202
            research_id = submitted.json()["job_id"]
            claimed = await repo.command(
                "claim", {"job_id": research_id, "lease_seconds": settings.job_lease_seconds}
            )
            await run_job(settings, repo, app.state.http, decode_row(claimed))
            research = await repo.owned_job(UUID(research_id), UUID(ids[0]))
            assert research["status"] == "completed", f"Web Chat failed: {research['error_code']}"
            assert research["report"]["verified"], "Research completed without verified evidence"
            assert len(research["report"]["sources"]) == 1
            print(json.dumps({"web_chat": "passed", "verified": True, "sources": 1}), flush=True)
            drafted = await api.post(
                "/api/posts/from-research",
                headers=sessions[0],
                json={"research_job_id": research_id},
            )
            assert drafted.status_code == 202
            draft_id = drafted.json()["job_id"]
            claimed = await repo.command(
                "claim", {"job_id": draft_id, "lease_seconds": settings.job_lease_seconds}
            )
            await run_job(settings, repo, app.state.http, decode_row(claimed))
            draft = await repo.owned_job(UUID(draft_id), UUID(ids[0]))
            assert draft["status"] == "completed", f"Post Agent failed: {draft['error_code']}"
            post_id, version_id = draft["report"]["post_id"], draft["report"]["version_id"]
            STATE.write_text(
                json.dumps({"user_ids": ids, "post_id": post_id, "version_id": version_id}),
                encoding="utf-8",
            )
            hidden = await api.get("/api/posts/" + post_id)
            assert hidden.status_code == 404
            denied = await api.post(
                "/api/posts/" + post_id + "/approve",
                headers=sessions[0],
                json={"version_id": version_id, "decision": "approve", "note": "smoke"},
            )
            assert denied.status_code == 403
            print(
                json.dumps(
                    {
                        "post_draft": "passed",
                        "draft_publicly_hidden": True,
                        "noneditor_approval_denied": True,
                    }
                ),
                flush=True,
            )
        print(json.dumps({"cleanup_state": str(STATE), "published_content": False}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Use test model/search doubles; keep real Auth, database and page fetching",
    )
    args = parser.parse_args()
    if args.deterministic:
        worker.make_context = deterministic_context
    print(
        json.dumps({"provider_mode": "deterministic" if args.deterministic else "live"}), flush=True
    )
    asyncio.run(main())
