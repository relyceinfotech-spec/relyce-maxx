from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import repository
from app.core.auth import User, current_user
from app.domain.models import ChatRequest
from app.services.jobs import Jobs

router = APIRouter(tags=["Chat"])


@router.post("/chat", status_code=202)
async def chat(body: ChatRequest, user: User = Depends(current_user), repo=Depends(repository)):
    return await Jobs(repo).submit_chat(user.id, body)


@router.get("/conversations")
async def conversations(user: User = Depends(current_user), repo=Depends(repository)):
    return {
        "items": await repo.many(
            """
        select id,title,created_at from conversations where user_id=%s
        order by created_at desc limit 100
    """,
            (user.id,),
        )
    }


@router.get("/conversations/{conversation_id}/messages")
async def messages(
    conversation_id: UUID, user: User = Depends(current_user), repo=Depends(repository)
):
    owned = await repo.one(
        "select id from conversations where id=%s and user_id=%s", (conversation_id, user.id)
    )
    if not owned:
        raise HTTPException(404, "Conversation not found")
    return {
        "items": await repo.many(
            """
        select id,role,content,created_at from messages where conversation_id=%s and user_id=%s
        order by created_at,id limit 500
    """,
            (conversation_id, user.id),
        )
    }
