from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.core.errors import LeaseLost


class Repository:
    """Parameterized database operations. Ownership is explicit at call sites.

    API handlers do not accept arbitrary table names or SQL from clients.
    """

    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def one(self, sql: str, params=()):
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params)).fetchone()

    async def many(self, sql: str, params=()):
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params)).fetchall()

    async def execute(self, sql: str, params=()):
        async with self.pool.connection() as conn:
            await conn.execute(sql, params)

    async def owned_job(self, job_id: UUID, user_id: UUID):
        return await self.one(
            "select * from research_jobs where id=%s and user_id=%s", (job_id, user_id)
        )

    async def claim(self):
        return await self.one(
            """
            with candidate as (
              select id from research_jobs where status='queued'
              or (status='running' and leased_until<now())
              order by created_at for update skip locked limit 1
            ) update research_jobs j set status='running',lease_id=gen_random_uuid(),
              leased_until=now()+(%s * interval '1 second'),attempts=attempts+1
            from candidate c where j.id=c.id returning j.*
        """,
            (self.lease_seconds,),
        )

    async def event(self, job, stage: str, data: dict):
        row = await self.one(
            """
            insert into research_events(job_id,user_id,stage,data)
            select id,user_id,%s,%s from research_jobs
            where id=%s and status='running' and lease_id=%s and leased_until>now()
            returning id
        """,
            (stage, Jsonb(data), job["id"], job["lease_id"]),
        )
        if not row:
            raise LeaseLost("Job cancelled or lease expired")

    async def finish(self, job, report: dict, run_id: UUID, follow_up=None):
        async with self.pool.connection() as conn, conn.transaction():
            row = await (
                await conn.execute(
                    """
                update research_jobs set status='completed',report=%s,finished_at=now(),
                  lease_id=null,leased_until=null where id=%s and lease_id=%s
                  and status='running' and leased_until>now() returning id
            """,
                    (Jsonb(report), job["id"], job["lease_id"]),
                )
            ).fetchone()
            if not row:
                raise LeaseLost("Job cancelled or lease expired")
            if job["conversation_id"] and job["kind"] == "chat":
                await conn.execute(
                    """
                    insert into messages(conversation_id,user_id,role,content)
                    values(%s,%s,'assistant',%s)
                """,
                    (job["conversation_id"], job["user_id"], report["answer"]),
                )
            await conn.execute(
                """
                update agent_runs set status='completed',finished_at=now() where id=%s
            """,
                (run_id,),
            )
            await conn.execute(
                """
                insert into research_events(job_id,user_id,stage,data)
                values(%s,%s,'completed',%s)
            """,
                (job["id"], job["user_id"], Jsonb({"verified": report.get("verified", False)})),
            )
            if follow_up:
                await conn.execute(
                    """
                    insert into agent_private.outbox(job_id,user_id,payload) values(%s,%s,%s)
                    on conflict(job_id) do nothing
                """,
                    (job["id"], job["user_id"], Jsonb(follow_up)),
                )

    async def fail(self, job, run_id: UUID, code: str):
        await self.execute(
            """
            update agent_runs set status='failed',error_code=%s,finished_at=now() where id=%s
        """,
            (code, run_id),
        )
        await self.one(
            """
            with failed as (
              update research_jobs set status='failed',error_code=%s,finished_at=now(),
                lease_id=null,leased_until=null where id=%s and lease_id=%s
                and status='running' returning id,user_id
            ) insert into research_events(job_id,user_id,stage,data)
              select id,user_id,'failed',%s from failed returning id
        """,
            (code, job["id"], job["lease_id"], Jsonb({"error_code": code})),
        )
