import time
from contextlib import asynccontextmanager

from psycopg.types.json import Jsonb

from app.core.errors import LeaseLost


class Trace:
    def __init__(self, repo, run_id):
        self.repo = repo
        self.run_id = run_id
        self.job = None

    async def reserve(self, budget):
        if not self.job or not self.job.get("id"):
            return
        counters = {
            key: getattr(budget, key)
            for key in ("searches", "pages", "model_calls", "tokens_reserved", "cost_reserved")
        }
        if self.job.get("payload", {}).get("autonomous"):
            await self.repo.one(
                "select agent_private.reserve_autonomous_budget(%s,%s,%s) as id",
                (self.job["id"], self.job["lease_id"], Jsonb(counters)),
            )
            return
        row = await self.repo.one(
            """
            update research_jobs set budget=%s where id=%s and lease_id=%s
              and status='running' and leased_until>now() returning id
        """,
            (Jsonb(counters), self.job["id"], self.job["lease_id"]),
        )
        if not row:
            raise LeaseLost("Job cancelled or lease expired before external call")

    @asynccontextmanager
    async def tool(self, name: str):
        started = time.monotonic()
        code = None
        try:
            yield
        except Exception as exc:
            code = type(exc).__name__
            raise
        finally:
            await self.repo.execute(
                """
                insert into tool_executions(run_id,tool,status,latency_ms,error_code)
                values(%s,%s,%s,%s,%s)
            """,
                (
                    self.run_id,
                    name,
                    "failed" if code else "completed",
                    int((time.monotonic() - started) * 1000),
                    code,
                ),
            )

    async def model(self, model, usage, latency_ms, reserved_cost, status):
        await self.repo.execute(
            """
            insert into model_usage(run_id,model,prompt_tokens,completion_tokens,
              reported_cost,reserved_cost,latency_ms,status) values(%s,%s,%s,%s,%s,%s,%s,%s)
        """,
            (
                self.run_id,
                model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("cost"),
                reserved_cost,
                latency_ms,
                status,
            ),
        )
        if self.job and self.job.get("id") and usage.get("output_ceiling"):
            ceiling = usage["output_ceiling"]
            await self.repo.event(
                self.job,
                "model_output",
                {
                    "model": model,
                    "status": status,
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "output_ceiling": ceiling,
                    "ceiling_utilization": usage.get("completion_tokens", 0) / ceiling,
                    "finish_reason": usage.get("finish_reason"),
                },
            )
