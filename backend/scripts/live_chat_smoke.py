"""Real Chat providers with disposable local SQL and local Auth; never publishes.

Launch with `node tools/local-db/test.mjs --live` from the workspace root.
Evidence and sanitized usage are saved locally, even when acceptance fails.
"""

import argparse
import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

import app.workers.runner as worker
from app.core.config import Settings
from app.core.runtime import event_loop
from app.database.checkpoints import SupabaseSaver
from app.database.repository import Repository
from app.database.supabase import SupabaseRepository, decode_row
from app.domain.models import ChatReport, Report, Source
from app.llm.provider_errors import provider_error
from app.main import create_app
from app.observability.usage import usage_summary
from app.research.audit import audit_evidence
from app.tools.fetcher import FetchRejected
from scripts.local_database import initialize_database

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (
        "normal",
        "Explain Python decorators in approximately 150 words. Include one small example. Do not add unnecessary sections.",
    ),
    ("standard", "Explain Python decorators and how functools.wraps preserves function metadata."),
    ("auto_web", "What's new in Python this week?"),
    ("explicit_search", "Search the web for recent LangGraph updates"),
    ("url", "Summarize https://docs.python.org/3/"),
    ("followup", "What were the key changes?"),
    ("deep", "Deep research Python 3.15 compatibility with AI frameworks"),
    ("unavailable_page", "Summarize https://unavailable.maxxx.invalid/article"),
]


class LocalTransport(httpx.AsyncBaseTransport):
    """SQL-backed local RPC/Auth; only authorized provider endpoints reach the network."""

    def __init__(self, repo, owner):
        self.repo, self.owner = repo, owner
        self.network = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        self.calls = []

    async def handle_async_request(self, request):
        if request.url.host != "offline.invalid":
            allowed = request.url.scheme == "https" and (
                (
                    request.url.host == "openrouter.ai"
                    and request.url.path
                    in ("/api/v1/chat/completions", "/api/v1/models", "/api/v1/key")
                )
                or (
                    request.url.host == "google.serper.dev"
                    and request.url.path in ("/search", "/news")
                )
            )
            if not allowed:
                raise RuntimeError("External endpoint blocked by live smoke harness")
            response = await self.network.handle_async_request(request)
            await response.aread()
            if request.method == "GET":
                return response
            body = json.loads(request.content)
            entry = {"provider": request.url.host, "status": response.status_code}
            if request.url.host == "openrouter.ai":
                entry["model"] = body.get("model")
                entry["max_tokens"] = body.get("max_tokens")
            if request.url.host == "google.serper.dev":
                entry["query"] = body.get("q")
                if response.is_success:
                    data = response.json()
                    entry.update(
                        result_count=len(data.get("organic", data.get("news", []))),
                        reported_credits=data.get("credits"),
                    )
            elif response.is_success:
                data = response.json()
                entry["usage"] = data.get("usage", {})
                choice = (data.get("choices") or [{}])[0]
                entry["finish_reason"] = choice.get("finish_reason")
                entry["content_characters"] = len(choice.get("message", {}).get("content") or "")
            else:
                entry["error"] = provider_error(response)
            self.calls.append(entry)
            return response
        if request.url.path == "/auth/v1/user":
            valid = request.headers.get("Authorization") == "Bearer " + str(self.owner)
            return (
                httpx.Response(200, json={"id": str(self.owner)}) if valid else httpx.Response(401)
            )
        payload = json.loads(request.content)
        if request.url.path.endswith("/maxxx_query"):
            sql, params = (
                "select public.maxxx_query(%s,%s) result",
                (payload["operation_id"], Jsonb(payload["parameters"])),
            )
        elif request.url.path.endswith("/maxxx_command"):
            if payload["operation"] not in {"submit_chat", "claim", "finish", "fail"}:
                raise RuntimeError("Publishing/discovery commands forbidden in smoke")
            sql, params = (
                "select public.maxxx_command(%s,%s) result",
                (payload["operation"], Jsonb(payload["payload"])),
            )
        elif request.url.path.endswith("/maxxx_checkpoint"):
            sql, params = (
                "select public.maxxx_checkpoint(%s,%s) result",
                (payload["operation"], Jsonb(payload["payload"])),
            )
        else:
            raise RuntimeError("Unknown local endpoint")
        try:
            async with self.repo.pool.connection() as conn, conn.transaction():
                await conn.execute("set local role service_role")
                row = await (await conn.execute(sql, params)).fetchone()
            return httpx.Response(
                200, content=json.dumps(row["result"]), headers={"content-type": "application/json"}
            )
        except psycopg.Error as exc:
            code = exc.sqlstate or ""
            return httpx.Response(
                int(code[2:]) if code.startswith("PT") else 500, json={"code": code}
            )

    async def aclose(self):
        await self.network.aclose()


def provider_settings():
    source = Settings()
    fields = {
        "openrouter_api_key",
        "serper_api_key",
        "model_chat",
        "model_fast",
        "model_research",
        "serper_gl",
        "serper_hl",
        "model_price_ceiling_per_million",
        "structured_output_mode",
        "model_reasoning_effort",
        "chat_router_output_tokens",
        "chat_normal_output_tokens",
        "chat_simple_output_tokens",
        "chat_coding_output_tokens",
        "chat_web_output_tokens",
        "chat_deep_output_tokens",
        "provider_credit_preflight_enabled",
        "chat_history_max_bytes",
        "chat_history_excerpt_bytes",
        "chat_web_success_pages",
        "chat_url_success_pages",
        "chat_deep_success_pages",
        "chat_web_search_iterations",
        "chat_deep_search_iterations",
    }
    settings = Settings(
        _env_file=None,
        **source.model_dump(include=fields),
        supabase_url="https://offline.invalid",
        supabase_publishable_key="local-public",
        supabase_secret_key="local-only",
        model_fallback="",
        model_embedding="",
        editor_user_ids=[],
        discovery_enabled=False,
        recheck_enabled=False,
        autonomous_publishing_enabled=False,
        decision_mode="off",
        max_output_tokens=2400,
        max_model_calls=6,
        max_searches=2,
        max_pages=4,
        deep_max_searches=6,
        deep_max_pages=8,
        max_total_tokens=96000,
        max_run_cost_usd=1.5,
        run_timeout_seconds=240,
        job_lease_seconds=360,
        http_timeout_seconds=40,
    )
    if settings.missing():
        raise RuntimeError("Missing setting names: " + ", ".join(settings.missing()))
    return settings


async def main(args):
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        raise RuntimeError("Launch through the disposable local database runner")
    initialize_database(url)
    settings = provider_settings()
    owner = uuid4()
    artifact = {
        "started_at": datetime.now(UTC).isoformat(),
        "database": "local_disposable",
        "models": {k: getattr(settings, k) for k in ("model_chat", "model_fast", "model_research")},
        "auth": "local_identity_double",
        "cases": [],
        "provider_calls": [],
        "publishing": False,
        "suite_reserved_limit_usd": args.max_reserved_cost,
    }
    ROOT.joinpath(".local-state").mkdir(exist_ok=True)
    output = (
        ROOT
        / ".local-state"
        / ("live-chat-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + ".json")
    )
    print(
        json.dumps(
            {
                "artifact": str(output),
                "models": artifact["models"],
                "first_prompt": selected_cases(args)[0][1],
            }
        ),
        flush=True,
    )
    pool = AsyncConnectionPool(
        url,
        open=False,
        min_size=1,
        max_size=1,
        kwargs={
            "row_factory": dict_row,
            "autocommit": True,
            "cursor_factory": psycopg.AsyncClientCursor,
        },
    )
    await pool.open(wait=True)
    actual_context = worker.make_context

    def diagnostic_context(*values):
        ctx = actual_context(*values)

        def save_diagnostic(entry):
            artifact.setdefault("evidence_diagnostics", []).append(
                {"job_id": str(ctx.job["id"]), **entry}
            )
            output.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")

        ctx.evidence_diagnostics = save_diagnostic
        return ctx

    worker.make_context = diagnostic_context
    repo = Repository(pool)
    await repo.execute("insert into auth.users(id) values(%s)", (owner,))
    transport = LocalTransport(repo, owner)
    remaining = args.max_reserved_cost
    conversations = {}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=40, trust_env=False) as http:
            response = await http.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            models = {m["id"]: m for m in response.json()["data"]}
            capabilities = {}
            for name in ("model_chat", "model_fast", "model_research"):
                selected = getattr(settings, name)
                if selected not in models:
                    raise RuntimeError("Configured model absent from OpenRouter catalog: " + name)
                capabilities[name] = models[selected].get("supported_parameters", [])
            if any(
                "structured_outputs" not in capabilities[name]
                for name in ("model_fast", "model_research")
            ):
                if any(
                    "response_format" not in capabilities[name]
                    for name in ("model_fast", "model_research")
                ):
                    raise RuntimeError(
                        "Configured research/router model does not advertise JSON output support"
                    )
                settings.structured_output_mode = "json_object"
            artifact["capabilities"] = capabilities
            artifact["structured_output_mode"] = settings.structured_output_mode
            print(
                json.dumps(
                    {
                        "provider_preflight": "passed",
                        "structured_output_mode": settings.structured_output_mode,
                    }
                ),
                flush=True,
            )
            rpc = SupabaseRepository(settings, http)
            app = create_app(settings)
            async with app.router.lifespan_context(app):
                app.state.repo, app.state.http = rpc, http
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://local.test"
                ) as api:
                    for name, prompt in selected_cases(args):
                        if remaining <= 0.1:
                            artifact["stopped_reason"] = "Suite reservation budget exhausted"
                            break
                        settings.max_run_cost_usd = min(1.5, remaining)
                        before = len(transport.calls)
                        body = {"prompt": prompt, "mode": "auto", "idempotency_key": str(uuid4())}
                        if name == "followup":
                            if "url" not in conversations:
                                artifact["cases"].append(
                                    {
                                        "case": name,
                                        "status": "skipped",
                                        "reason": "No completed preceding URL conversation",
                                    }
                                )
                                continue
                            body["conversation_id"] = conversations["url"]
                        if name == "unavailable_page":

                            def failed_page_context(*values):
                                ctx = diagnostic_context(*values)

                                class Unavailable:
                                    async def fetch(self, target):
                                        raise FetchRejected("Simulated unavailable webpage")

                                ctx.fetcher = Unavailable()
                                return ctx

                            worker.make_context = failed_page_context
                        submitted = await api.post(
                            "/api/chat",
                            headers={"Authorization": "Bearer " + str(owner)},
                            json=body,
                        )
                        if submitted.status_code != 202:
                            raise RuntimeError(
                                "Local chat submission HTTP " + str(submitted.status_code)
                            )
                        job_id = submitted.json()["job_id"]
                        claimed = decode_row(
                            await rpc.command("claim", {"job_id": job_id, "lease_seconds": 360})
                        )
                        print(json.dumps({"case": name, "phase": "running"}), flush=True)
                        await worker.run_job(settings, rpc, http, claimed)
                        worker.make_context = diagnostic_context
                        final = await rpc.owned_job(UUID(job_id), owner)
                        saved = await SupabaseSaver(rpc).aget_tuple(
                            {
                                "configurable": {
                                    "thread_id": f"{owner}:{job_id}:{claimed['checkpoint_generation']}"
                                }
                            }
                        )
                        events = await repo.many(
                            "select stage,data from research_events where job_id=%s order by id",
                            (UUID(job_id),),
                        )
                        usage = await repo.many(
                            "select u.model,u.prompt_tokens,u.completion_tokens,u.reported_cost,u.reserved_cost,u.status from model_usage u join agent_runs r on r.id=u.run_id where r.job_id=%s order by u.created_at",
                            (UUID(job_id),),
                        )
                        tools = await repo.many(
                            "select t.tool,t.status,t.error_code,t.latency_ms from tool_executions t join agent_runs r on r.id=t.run_id where r.job_id=%s order by t.created_at",
                            (UUID(job_id),),
                        )
                        report = final.get("report") or {}
                        state = saved.checkpoint["channel_values"] if saved else {}
                        sources = state.get("sources", [])
                        case = {
                            "case": name,
                            "prompt": prompt,
                            "status": final["status"],
                            "error_code": final.get("error_code"),
                            "route": saved.checkpoint["channel_values"].get("route")
                            if saved
                            else None,
                            "budget": final["budget"],
                            "report": report,
                            "retrieved_sources": sources,
                            "evidence_passages": state.get("evidence_passages", []),
                            "reader_pages": state.get("reader_pages", []),
                            "verification_result": state.get("verification_result", {}),
                            "completed_queries": state.get("completed_queries", []),
                            "history_message_count": len(state.get("history", [])),
                            "events": events,
                            "model_usage": usage,
                            "usage_summary": usage_summary(usage, final["budget"]),
                            "tools": tools,
                            "provider_calls": transport.calls[before:],
                        }
                        reserved = float(final["budget"].get("cost_reserved", 0))
                        case["evidence_audit"] = audit_evidence(
                            (
                                ChatReport if state.get("evidence_passages") is not None else Report
                            ).model_validate(report)
                            if report
                            else None,
                            [Source.model_validate(source) for source in sources],
                            state.get("evidence_passages"),
                        )
                        remaining -= reserved
                        case["checks"] = {
                            "real_model_response": any(u["status"] == "completed" for u in usage),
                            "serper_called": any(
                                c["provider"] == "google.serper.dev" for c in case["provider_calls"]
                            ),
                            "readable_source_count": len(sources),
                            "verified": report.get("verified", False),
                            "citation_count": len(
                                re.findall(
                                    r"\[[^\]]+\]\(https?://[^)]+\)", report.get("answer", "")
                                )
                            ),
                            "actual_model_cost_available": any(
                                u["reported_cost"] is not None for u in usage
                            ),
                        }
                        checks = case["checks"]
                        route = (case["route"] or {}).get("mode")
                        if name in ("normal", "standard"):
                            passed = (
                                final["status"] == "completed"
                                and route == "normal"
                                and not checks["serper_called"]
                                and bool(report.get("answer"))
                                and case["provider_calls"][-1].get("finish_reason") == "stop"
                            )
                        elif name == "unavailable_page":
                            passed = (
                                final["status"] == "completed"
                                and not report.get("verified")
                                and not report.get("claims")
                                and "evidence" in report.get("answer", "").lower()
                            )
                        else:
                            passed = (
                                final["status"] == "completed"
                                and checks["verified"]
                                and checks["citation_count"] > 0
                            )
                            if name in ("python_freshness", "auto_web", "explicit_search"):
                                passed = passed and route == "web" and checks["serper_called"]
                            if name == "url":
                                passed = (
                                    passed
                                    and not checks["serper_called"]
                                    and any(
                                        s["url"].rstrip("/") == "https://docs.python.org/3"
                                        for s in sources
                                    )
                                )
                            if name == "deep":
                                passed = (
                                    passed
                                    and route == "deep"
                                    and final["budget"].get("searches", 0) >= 2
                                    and len(sources) >= 2
                                )
                            if name == "followup":
                                passed = passed and case["history_message_count"] >= 2
                        case["workflow_checks_passed"] = bool(passed)
                        case["answer_quality_review"] = (
                            "pending" if passed and report.get("answer") else "not_accepted"
                        )
                        artifact["cases"].append(case)
                        artifact["provider_calls"] = transport.calls
                        output.write_text(
                            json.dumps(artifact, indent=2, default=str), encoding="utf-8"
                        )
                        print(
                            json.dumps(
                                {
                                    "case": name,
                                    "status": case["status"],
                                    "error_code": case["error_code"],
                                    "checks": case["checks"],
                                }
                            ),
                            flush=True,
                        )
                        if final["status"] == "completed":
                            conversations[name] = submitted.json()["conversation_id"]
                        if final.get("error_code") in {
                            "ProviderHTTP401",
                            "ProviderHTTP402",
                            "ProviderHTTP403",
                            "ProviderHTTP404",
                            "ProviderHTTP429",
                        }:
                            artifact["stopped_reason"] = (
                                final["error_code"]
                                + ": stopped without payment/auth/rate-limit retries"
                            )
                            break
            assert (await repo.one("select count(*) n from posts"))["n"] == 0
            artifact["published_or_drafted_posts"] = 0
    finally:
        worker.make_context = actual_context
        artifact["provider_calls"] = transport.calls
        artifact["finished_at"] = datetime.now(UTC).isoformat()
        selected = selected_cases(args)
        artifact["not_run"] = [
            name for name, _ in selected if name not in {c["case"] for c in artifact["cases"]}
        ]
        artifact["workflow_acceptance_passed"] = not artifact["not_run"] and all(
            c.get("workflow_checks_passed", False) for c in artifact["cases"]
        )
        output.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
        await pool.close()
    print(
        json.dumps(
            {
                "artifact": str(output),
                "completed_cases": len(artifact["cases"]),
                "stopped_reason": artifact.get("stopped_reason"),
                "hosted_database_writes": 0,
            }
        ),
        flush=True,
    )
    return 0 if artifact["workflow_acceptance_passed"] else 1


def selected_cases(args):
    if args.case:
        return [(name, prompt) for name, prompt in CASES if name == args.case]
    return CASES if args.suite else CASES[:1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case", choices=[name for name, _ in CASES], help="Run only one named case"
    )
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Continue through web, URL, follow-up and deep research after normal chat",
    )
    parser.add_argument(
        "--max-reserved-cost",
        type=float,
        default=3.0,
        help="Conservative suite reservation cap in USD; not an actual charge estimate",
    )
    args = parser.parse_args()
    if not 0 < args.max_reserved_cost <= 10:
        parser.error("max-reserved-cost must be in (0, 10]")
    raise SystemExit(asyncio.run(main(args), loop_factory=event_loop))
