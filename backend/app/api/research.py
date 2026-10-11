import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import repository
from app.core.auth import User, current_user
from app.services.jobs import Jobs

router = APIRouter(prefix="/research", tags=["Research"])


async def owned(repo, job_id, user_id):
    job = await repo.owned_job(job_id, user_id)
    if not job:
        raise HTTPException(404, "Research not found")
    return job


@router.get("/{job_id}")
async def get_job(job_id: UUID, user: User = Depends(current_user), repo=Depends(repository)):
    job = await owned(repo, job_id, user.id)
    return {
        key: job[key]
        for key in (
            "id",
            "conversation_id",
            "kind",
            "mode",
            "status",
            "report",
            "error_code",
            "created_at",
            "finished_at",
        )
    }


@router.post("/{job_id}/cancel")
async def cancel(job_id: UUID, user: User = Depends(current_user), repo=Depends(repository)):
    return await Jobs(repo).cancel(job_id, user.id)


@router.post("/{job_id}/retry", status_code=202)
async def retry(job_id: UUID, user: User = Depends(current_user), repo=Depends(repository)):
    return await Jobs(repo).retry(job_id, user.id)


def sse(stage, data, event_id=None):
    return (
        (f"id: {event_id}\n" if event_id is not None else "")
        + f"event: {stage}\ndata: "
        + json.dumps(data, default=str)
        + "\n\n"
    )


@router.get("/{job_id}/events")
async def events(
    job_id: UUID,
    request: Request,
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
    user: User = Depends(current_user),
    repo=Depends(repository),
):
    job = await owned(repo, job_id, user.id)
    try:
        cursor = int(last_event_id or "0")
        if cursor < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID must be a nonnegative integer") from exc

    async def stream():
        nonlocal cursor
        yield sse("research.snapshot", {"job_id": job_id, "status": job["status"]})
        started = time.monotonic()
        while time.monotonic() - started < 600:
            if await request.is_disconnected():
                break
            rows = await repo.many(
                """
                select id,stage,data,created_at from research_events where job_id=%s and user_id=%s
                and id>%s order by id limit 100
            """,
                (job_id, user.id, cursor),
            )
            for row in rows:
                cursor = row["id"]
                yield sse("research.step", row, cursor)
            current = await repo.owned_job(job_id, user.id)
            if not current or (
                current["status"] in ("completed", "failed", "cancelled") and len(rows) < 100
            ):
                if current:
                    pending = await repo.one(
                        """
                        select id from research_events where job_id=%s and user_id=%s
                        and id>%s order by id limit 1
                    """,
                        (job_id, user.id, cursor),
                    )
                    if pending:
                        continue
                yield sse("research.done", {"status": current["status"] if current else "deleted"})
                break
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
