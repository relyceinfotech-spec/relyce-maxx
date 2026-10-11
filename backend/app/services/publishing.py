from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.core.errors import EvidenceError, LeaseLost
from app.database.supabase import decode_row
from app.domain.models import Draft, Report
from app.research.verification import validate_claims


class Publishing:
    def __init__(self, repo):
        self.repo = repo

    async def save_draft(self, job, draft: Draft, report: Report):
        validate_claims(draft.claims, report.sources)
        if hasattr(self.repo, "command"):
            row = await self.repo.command(
                "save_draft",
                {
                    "job_id": str(job["id"]),
                    "lease_id": str(job["lease_id"]),
                    "draft": draft.model_dump(mode="json"),
                    "sources": [s.model_dump(mode="json") for s in report.sources],
                },
            )
            if not row:
                raise LeaseLost("Draft job cancelled or lease expired")
            return decode_row(row)
        async with self.repo.pool.connection() as conn, conn.transaction():
            lease = await (
                await conn.execute(
                    """
                select id from research_jobs where id=%s and status='running' and lease_id=%s
                and leased_until>now() for update
            """,
                    (job["id"], job["lease_id"]),
                )
            ).fetchone()
            if not lease:
                raise LeaseLost("Draft job cancelled or lease expired")
            existing = await (
                await conn.execute(
                    "select id,post_id from post_versions where draft_job_id=%s", (job["id"],)
                )
            ).fetchone()
            if existing:
                return existing
            payload = job["payload"]
            topics = await (
                await conn.execute(
                    "select id,slug from topics where slug=any(%s) and is_active",
                    (draft.topic_slugs,),
                )
            ).fetchall()
            if len(topics) != len(set(draft.topic_slugs)):
                raise EvidenceError("Draft uses unknown topics")
            research_id = UUID(payload["research_job_id"])
            await conn.execute(
                "select pg_advisory_xact_lock(hashtextextended(%s,0))", (str(research_id),)
            )
            if payload.get("post_id"):
                post = await (
                    await conn.execute(
                        "select * from posts where id=%s for update", (UUID(payload["post_id"]),)
                    )
                ).fetchone()
                previous = str(post["current_version_id"]) if post["current_version_id"] else None
                if previous != payload.get("previous_version_id"):
                    raise EvidenceError("Post changed while revision was being drafted")
            else:
                post = await (
                    await conn.execute(
                        """
                    insert into posts(created_by,research_job_id,cluster_id) values(%s,%s,%s)
                    on conflict(research_job_id) do update set research_job_id=excluded.research_job_id
                    returning *
                """,
                        (job["user_id"], research_id, payload.get("cluster_id")),
                    )
                ).fetchone()
            version = await (
                await conn.execute(
                    """
                insert into post_versions(post_id,research_job_id,draft_job_id,previous_version_id,
                  title,summary,body,claims,sources,topic_slugs,correction_note)
                values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id,post_id
            """,
                    (
                        post["id"],
                        research_id,
                        job["id"],
                        post["current_version_id"],
                        draft.title,
                        draft.summary,
                        draft.body,
                        Jsonb([c.model_dump() for c in draft.claims]),
                        Jsonb([s.model_dump() for s in report.sources]),
                        draft.topic_slugs,
                        payload.get("correction_note"),
                    ),
                )
            ).fetchone()
            return version

    async def review(self, post_id, user, review):
        autonomous = await self.repo.one(
            """select j.payload from post_versions v join research_jobs j on j.id=v.draft_job_id
            where v.id=%s and v.post_id=%s""",
            (review.version_id, post_id),
        )
        if autonomous and autonomous["payload"].get("autonomous"):
            raise HTTPException(409, "Autonomous posts must pass the controlled publisher")
        if hasattr(self.repo, "command"):
            if review.decision == "approve":
                version = await self.repo.one(
                    "select * from post_versions where id=%s and post_id=%s",
                    (review.version_id, post_id),
                )
                if not version:
                    raise HTTPException(404, "Post version not found")
                research = await self.repo.one(
                    "select report from research_jobs where id=%s and status='completed'",
                    (version["research_job_id"],),
                )
                if not research or not research["report"].get("verified"):
                    raise HTTPException(409, "Publication requires verified research")
                from app.domain.models import Claim

                validate_claims(
                    [Claim(**c) for c in version["claims"]], Report(**research["report"]).sources
                )
            return await self.repo.command(
                "review",
                {
                    "post_id": str(post_id),
                    "user_id": str(user.id),
                    **review.model_dump(mode="json"),
                },
            )
        async with self.repo.pool.connection() as conn, conn.transaction():
            post = await (
                await conn.execute("select * from posts where id=%s for update", (post_id,))
            ).fetchone()
            version = await (
                await conn.execute(
                    "select * from post_versions where id=%s and post_id=%s for update",
                    (review.version_id, post_id),
                )
            ).fetchone()
            if not post or not version:
                raise HTTPException(404, "Post version not found")
            old = await (
                await conn.execute(
                    "select decision from post_reviews where version_id=%s", (review.version_id,)
                )
            ).fetchone()
            if old:
                if old["decision"] != review.decision:
                    raise HTTPException(409, "Version already reviewed")
                return {
                    "post_id": post_id,
                    "version_id": review.version_id,
                    "decision": review.decision,
                }
            if version["previous_version_id"] != post["current_version_id"]:
                raise HTTPException(409, "Stale revision; generate against the current version")
            if review.decision == "approve":
                research = await (
                    await conn.execute(
                        "select report from research_jobs where id=%s and status='completed'",
                        (version["research_job_id"],),
                    )
                ).fetchone()
                if not research or not research["report"].get("verified"):
                    raise HTTPException(409, "Publication requires verified research")
                report = Report(**research["report"])
                from app.domain.models import Claim

                validate_claims([Claim(**c) for c in version["claims"]], report.sources)
                topics = await (
                    await conn.execute(
                        "select id,slug from topics where slug=any(%s) and is_active",
                        (version["topic_slugs"],),
                    )
                ).fetchall()
                if len(topics) != len(set(version["topic_slugs"])):
                    raise HTTPException(409, "Invalid topic references")
                await conn.execute("delete from post_topics where post_id=%s", (post_id,))
                for topic in topics:
                    await conn.execute(
                        "insert into post_topics values(%s,%s,%s)",
                        (post_id, topic["id"], topic["slug"] == version["topic_slugs"][0]),
                    )
                await conn.execute(
                    """
                    update posts set status='published',current_version_id=%s,
                      published_at=coalesce(published_at,now()) where id=%s
                """,
                    (review.version_id, post_id),
                )
            await conn.execute(
                "update post_versions set status=%s where id=%s",
                ("approved" if review.decision == "approve" else "rejected", review.version_id),
            )
            await conn.execute(
                """
                insert into post_reviews(post_id,version_id,reviewer_id,decision,note) values(%s,%s,%s,%s,%s)
            """,
                (post_id, review.version_id, user.id, review.decision, review.note),
            )
            return {
                "post_id": post_id,
                "version_id": review.version_id,
                "decision": review.decision,
            }
