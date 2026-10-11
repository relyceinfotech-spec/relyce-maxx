from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.dependencies import repository
from app.core.auth import User, current_user
from app.core.config import Settings
from app.main import create_app


def test_terminal_event_committed_between_poll_reads_is_delivered():
    user = User(uuid4(), False)
    job_id = uuid4()

    class Repo:
        polls = 0

        async def owned_job(self, job, owner):
            assert job == job_id and owner == user.id
            return {"status": "completed"}

        async def many(self, sql, params):
            self.polls += 1
            if self.polls == 1:
                return []
            return [
                {
                    "id": 1,
                    "stage": "completed",
                    "data": {"verified": True},
                    "created_at": "2026-10-11",
                }
            ]

        async def one(self, sql, params):
            return {"id": 1} if self.polls == 1 else None

    app = create_app(Settings(_env_file=None))
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[repository] = lambda: Repo()
    with TestClient(app) as client:
        response = client.get(f"/api/research/{job_id}/events")
        assert "id: 1\nevent: research.step" in response.text
        assert response.text.index("research.step") < response.text.index("research.done")
