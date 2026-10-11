import json
from uuid import uuid4

import httpx
from pydantic import SecretStr

from app.services.autonomous import AutonomousPublisher


async def test_publisher_refreshes_session_without_server_key(settings):
    owner = uuid4()
    settings.publisher_user_id = owner
    settings.autonomous_publishing_enabled = True
    settings.supabase_url = "https://offline.invalid"
    settings.supabase_publishable_key = SecretStr("publishable")
    settings.supabase_secret_key = SecretStr("must-never-be-sent")
    settings.publisher_refresh_token = SecretStr("old-refresh")
    settings.publisher_email = "publisher@example.invalid"
    settings.publisher_password = SecretStr("offline-password")
    calls = []

    def handler(request):
        assert request.headers["apikey"] == "publishable"
        assert "must-never-be-sent" not in str(request.headers)
        calls.append(request.url.path)
        if request.url.path == "/auth/v1/token":
            if request.url.params["grant_type"] == "refresh_token":
                return httpx.Response(401)
            assert json.loads(request.content)["email"] == "publisher@example.invalid"
            return httpx.Response(
                200,
                json={
                    "access_token": "dedicated-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                },
            )
        assert request.headers["authorization"] == "Bearer dedicated-access"
        return httpx.Response(200, json={"published": True})

    job = {"id": uuid4(), "user_id": owner, "lease_id": uuid4()}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        publisher = AutonomousPublisher(settings, http)
        assert (await publisher.publish(job, uuid4()))["published"]
        await publisher.publish(job, uuid4())
    assert calls == [
        "/auth/v1/token",
        "/auth/v1/token",
        "/rest/v1/rpc/maxxx_autopublish",
        "/rest/v1/rpc/maxxx_autopublish",
    ]
