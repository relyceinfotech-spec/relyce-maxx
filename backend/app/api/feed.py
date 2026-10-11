from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import repository
from app.core.auth import User, current_user
from app.domain.models import Interaction, Interest
from app.services.feed import Feed

router = APIRouter(tags=["Feed & preferences"])


@router.get("/feed")
async def feed(
    mode: Literal["for_you", "following", "latest"] = "for_you",
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(current_user),
    repo=Depends(repository),
):
    return await Feed(repo).get(user.id, mode, limit)


@router.get("/topics")
async def topics(repo=Depends(repository)):
    return {
        "items": await repo.many(
            "select id,parent_id,slug,name from topics where is_active order by slug"
        )
    }


@router.get("/me/interests")
async def interests(user: User = Depends(current_user), repo=Depends(repository)):
    return {
        "items": await repo.many(
            "select topic_id,weight,blocked from user_interests where user_id=%s", (user.id,)
        )
    }


@router.put("/me/interests")
async def set_interests(
    body: list[Interest], user: User = Depends(current_user), repo=Depends(repository)
):
    if len(body) > 100 or len({i.topic_id for i in body}) != len(body):
        raise HTTPException(422, "Maximum 100 unique topic interests")
    if hasattr(repo, "command"):
        return await repo.command(
            "set_interests",
            {"user_id": str(user.id), "interests": [i.model_dump(mode="json") for i in body]},
        )
    async with repo.pool.connection() as conn, conn.transaction():
        ids = [i.topic_id for i in body]
        valid = await (
            await conn.execute("select id from topics where id=any(%s) and is_active", (ids,))
        ).fetchall()
        if len(valid) != len(ids):
            raise HTTPException(422, "Unknown topic")
        await conn.execute("delete from user_interests where user_id=%s", (user.id,))
        for interest in body:
            await conn.execute(
                "insert into user_interests(user_id,topic_id,weight,blocked) values(%s,%s,%s,%s)",
                (user.id, interest.topic_id, interest.weight, interest.blocked),
            )
    return {"saved": len(body)}


@router.post("/me/interactions")
async def interaction(
    body: Interaction, user: User = Depends(current_user), repo=Depends(repository)
):
    post = await repo.one(
        "select id from posts where id=%s and status='published'", (body.post_id,)
    )
    if not post:
        raise HTTPException(404, "Published post not found")
    await repo.execute(
        """
        insert into user_interactions(user_id,post_id,kind) values(%s,%s,%s)
        on conflict(user_id,post_id,kind) do update set created_at=now()
    """,
        (user.id, body.post_id, body.kind),
    )
    return {"saved": True}


@router.delete("/me/interactions")
async def remove_interaction(
    body: Interaction, user: User = Depends(current_user), repo=Depends(repository)
):
    await repo.execute(
        "delete from user_interactions where user_id=%s and post_id=%s and kind=%s",
        (user.id, body.post_id, body.kind),
    )
    return {"removed": True}
