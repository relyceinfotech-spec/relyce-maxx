from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.database.supabase import decode_row


class Jobs:
    def __init__(self, repo):
        self.repo = repo

    async def submit_chat(self, user_id, request):
        if hasattr(self.repo, "command"):
            row = await self.repo.command(
                "submit_chat", {**request.model_dump(mode="json"), "user_id": str(user_id)}
            )
            return self.response(decode_row(row))
        async with self.repo.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{user_id}:{request.idempotency_key}",),
            )
            existing = await (
                await conn.execute(
                    "select * from research_jobs where user_id=%s and idempotency_key=%s",
                    (user_id, request.idempotency_key),
                )
            ).fetchone()
            if existing:
                if (
                    existing["prompt"] != request.prompt
                    or existing["mode"] != request.mode
                    or existing["post_id"] != request.post_id
                    or (
                        request.conversation_id
                        and existing["conversation_id"] != request.conversation_id
                    )
                ):
                    raise HTTPException(409, "Idempotency key already used for a different request")
                return self.response(existing)
            if request.conversation_id:
                conversation = await (
                    await conn.execute(
                        "select id from conversations where id=%s and user_id=%s for update",
                        (request.conversation_id, user_id),
                    )
                ).fetchone()
                if not conversation:
                    raise HTTPException(404, "Conversation not found")
            else:
                conversation = await (
                    await conn.execute(
                        "insert into conversations(user_id,title) values(%s,%s) returning id",
                        (user_id, request.prompt[:120]),
                    )
                ).fetchone()
            if request.post_id:
                post = await (
                    await conn.execute(
                        "select id from posts where id=%s and status='published'",
                        (request.post_id,),
                    )
                ).fetchone()
                if not post:
                    raise HTTPException(404, "Published post not found")
            active = await (
                await conn.execute(
                    "select 1 from research_jobs where conversation_id=%s and status in ('queued','running')",
                    (conversation["id"],),
                )
            ).fetchone()
            if active:
                raise HTTPException(409, "Wait for the active conversation job or cancel it")
            await conn.execute(
                """
                insert into messages(user_id,conversation_id,role,content) values(%s,%s,'user',%s)
            """,
                (user_id, conversation["id"], request.prompt),
            )
            job = await (
                await conn.execute(
                    """
                insert into research_jobs(user_id,conversation_id,prompt,mode,post_id,idempotency_key)
                values(%s,%s,%s,%s,%s,%s) returning *
            """,
                    (
                        user_id,
                        conversation["id"],
                        request.prompt,
                        request.mode,
                        request.post_id,
                        request.idempotency_key,
                    ),
                )
            ).fetchone()
            return self.response(job)

    @staticmethod
    def response(job):
        return {
            "job_id": job["id"],
            "conversation_id": job["conversation_id"],
            "status": job["status"],
            "events_url": f"/api/research/{job['id']}/events",
        }

    async def cancel(self, job_id, user_id):
        row = await self.repo.one(
            """
            with cancelled as (
              update research_jobs set status='cancelled',lease_id=null,leased_until=null,
                finished_at=now() where id=%s and user_id=%s and status in ('queued','running')
              returning id,user_id
            ) insert into research_events(job_id,user_id,stage,data)
            select id,user_id,'cancelled','{}' from cancelled returning id
        """,
            (job_id, user_id),
        )
        if not row and not await self.repo.owned_job(job_id, user_id):
            raise HTTPException(404, "Research not found")
        return {"cancelled": bool(row)}

    async def retry(self, job_id, user_id):
        # Explicit retry restarts the graph under a fresh attempt key; never automatic on errors.
        row = await self.repo.one(
            """
            update research_jobs set status='queued',error_code=null,finished_at=null,
              checkpoint_generation=checkpoint_generation+1,budget='{}'::jsonb
            where id=%s and user_id=%s and status='failed' returning *
        """,
            (job_id, user_id),
        )
        if not row:
            raise HTTPException(409, "Only an owned failed job can be retried")
        return self.response(row)

    async def from_research(self, user, request):
        report = await self.repo.owned_job(request.research_job_id, user.id)
        if not report:
            raise HTTPException(404, "Research not found")
        if report["status"] != "completed" or not report["report"].get("verified"):
            raise HTTPException(409, "Research must be completed and verified")
        previous = None
        if request.post_id:
            post = await self.repo.one("select * from posts where id=%s", (request.post_id,))
            if not post or (post["created_by"] != user.id and not user.editor):
                raise HTTPException(404, "Post not found")
            if not request.correction_note:
                raise HTTPException(422, "A revision needs a correction/update note")
            previous = str(post["current_version_id"]) if post["current_version_id"] else None
        key = uuid5(NAMESPACE_URL, f"draft:{request.research_job_id}:{request.post_id}:{previous}")
        job = await self.repo.one(
            """
            insert into research_jobs(user_id,prompt,mode,kind,payload,idempotency_key)
            values(%s,'Generate an evidence-backed post draft','normal','draft',%s,%s)
            on conflict(user_id,idempotency_key) do update set idempotency_key=excluded.idempotency_key
            returning *
        """,
            (
                user.id,
                Jsonb(
                    {
                        "research_job_id": str(request.research_job_id),
                        "post_id": str(request.post_id) if request.post_id else None,
                        "previous_version_id": previous,
                        "correction_note": request.correction_note,
                        "cluster_id": report["payload"].get("cluster_id"),
                        "autonomous": report["payload"].get("autonomous") is True,
                    }
                ),
                key,
            ),
        )
        return self.response(job)
