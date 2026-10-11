from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.api.dependencies import repository
from app.core.auth import User, current_user
from app.core.config import Settings
from app.main import create_app


def test_boots_without_keys_and_reports_not_ready():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/health").json()["status"] == "ok"
        response = client.get("/ready")
        assert response.status_code == 503
        assert "OPENROUTER_API_KEY" in response.json()["missing_configuration"]
        assert client.post("/api/chat", json={}).status_code == 401
        schema = client.get("/openapi.json").json()
        assert "/api/chat" in schema["paths"]
        assert "/api/admin/agent-runs" in schema["paths"]


def test_foreign_job_returns_404():
    app = create_app(Settings(_env_file=None))
    user = User(uuid4(), False)

    class Repo:
        async def owned_job(self, job_id, user_id):
            assert user_id == user.id
            return None

    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[repository] = lambda: Repo()
    with TestClient(app) as client:
        assert client.get(f"/api/research/{uuid4()}").status_code == 404


def test_non_editor_cannot_access_admin_or_publish():
    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[current_user] = lambda: User(uuid4(), False)
    with TestClient(app) as client:
        assert client.get("/api/admin/agent-runs").status_code == 403
        assert client.post(f"/api/posts/{uuid4()}/approve", json={}).status_code == 403


def test_auth_uses_verified_user_not_client_metadata():
    from pydantic import SecretStr

    user_id = uuid4()
    settings = Settings(
        _env_file=None,
        supabase_url="https://supabase.example",
        supabase_publishable_key=SecretStr("test"),
    )

    def handler(request):
        assert request.url.path == "/auth/v1/user"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"id": str(user_id), "user_metadata": {"editor": True}})

    app = create_app(settings)
    with TestClient(app) as client:
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert (
            client.get(
                "/api/admin/agent-runs", headers={"Authorization": "Bearer test-token"}
            ).status_code
            == 403
        )
        client.portal.call(app.state.http.aclose)
        app.state.http = original
