import time
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class User:
    id: UUID
    editor: bool


async def current_user(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
) -> User:
    if not credentials:
        raise HTTPException(401, "Supabase bearer token required")
    settings = request.app.state.settings
    if not settings.supabase_url or not settings.supabase_publishable_key.get_secret_value():
        raise HTTPException(503, "Supabase Auth is not configured")
    # Auth server verifies signature, expiry and revocation. Never trust client metadata.
    try:
        response = await request.app.state.http.get(
            settings.supabase_url.rstrip("/") + "/auth/v1/user",
            headers={
                "apikey": settings.supabase_publishable_key.get_secret_value(),
                "Authorization": f"Bearer {credentials.credentials}",
            },
        )
        if response.status_code in (401, 403):
            raise HTTPException(401, "Invalid or expired token")
        response.raise_for_status()
        user_id = UUID(response.json()["id"])
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Authentication service unavailable") from exc
    repo = request.app.state.repo
    if repo:
        window = int(time.time() // 60)
        count = await repo.one(
            """
            insert into agent_private.api_rate_windows(user_id,bucket,requests) values(%s,%s,1)
            on conflict(user_id,bucket) do update set requests=api_rate_windows.requests+1
            returning requests
        """,
            (user_id, window),
        )
        if count["requests"] > settings.api_requests_per_minute:
            raise HTTPException(429, "Request rate limit reached", headers={"Retry-After": "60"})
        await repo.execute(
            "delete from agent_private.api_rate_windows where bucket<%s", (window - 2,)
        )
    return User(user_id, user_id in settings.editor_user_ids)


async def editor_user(user: User = Depends(current_user)) -> User:
    if not user.editor:
        raise HTTPException(403, "Editor access required")
    return user
