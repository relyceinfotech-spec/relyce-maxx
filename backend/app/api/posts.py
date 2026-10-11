from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import repository
from app.core.auth import User, current_user, editor_user
from app.domain.models import PostFromResearch, Review
from app.services.jobs import Jobs
from app.services.publishing import Publishing

router = APIRouter(prefix="/posts", tags=["Publishing"])


@router.post("/from-research", status_code=202)
async def from_research(
    body: PostFromResearch, user: User = Depends(current_user), repo=Depends(repository)
):
    return await Jobs(repo).from_research(user, body)


@router.post("/{post_id}/approve")
async def approve(
    post_id: UUID, body: Review, user: User = Depends(editor_user), repo=Depends(repository)
):
    return await Publishing(repo).review(post_id, user, body)


@router.get("/{post_id}")
async def get_post(post_id: UUID, repo=Depends(repository)):
    row = await repo.one(
        """
        select p.id,p.published_at,p.current_version_id,v.title,v.summary,v.body,v.claims,
          v.correction_note,v.topic_slugs from posts p join post_versions v on v.id=p.current_version_id
        where p.id=%s and p.status='published' and v.status='approved'
    """,
        (post_id,),
    )
    if not row:
        raise HTTPException(404, "Published post not found")
    sources = await repo.one(
        "select sources from post_versions where id=%s", (row["current_version_id"],)
    )
    row["sources"] = [
        {"id": s["id"], "url": s["url"], "title": s["title"]} for s in sources["sources"]
    ]
    row["followups"] = await repo.many(
        "select prompt from post_followups where version_id=%s order by position",
        (row["current_version_id"],),
    )
    return row


@router.get("/{post_id}/versions")
async def versions(post_id: UUID, user: User = Depends(current_user), repo=Depends(repository)):
    post = await repo.one("select id,created_by,status from posts where id=%s", (post_id,))
    if not post or (
        post["status"] != "published" and post["created_by"] != user.id and not user.editor
    ):
        raise HTTPException(404, "Post not found")
    return {
        "items": await repo.many(
            """
        select id,previous_version_id,title,summary,body,claims,topic_slugs,correction_note,status,created_at
        from post_versions where post_id=%s and (status='approved' or %s)
        order by created_at,id
    """,
            (post_id, user.editor or post["created_by"] == user.id),
        )
    }
