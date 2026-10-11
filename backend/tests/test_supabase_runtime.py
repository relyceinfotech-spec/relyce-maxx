from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core.config import Settings
from app.database.supabase import SupabaseRepository, decode_row, parameter, statement_id
from app.domain.models import ChatRequest
from app.services.jobs import Jobs


def test_api_transport_needs_existing_server_key_instead_of_database_url():
    settings = Settings(_env_file=None, supabase_secret_key=SecretStr("server-test"))
    assert "DATABASE_URL" not in settings.missing()
    assert "SUPABASE_SECRET_KEY" not in settings.missing()
    settings.database_transport = "postgres"
    assert "DATABASE_URL" in settings.missing()


async def test_rpc_transmits_fixed_operation_id_and_preserves_untrusted_parameter_text():
    owner = uuid4()
    needle = "'); drop table auth.users; --"

    def handler(request):
        import json

        body = json.loads(request.content)
        assert request.url.path == "/rest/v1/rpc/maxxx_query"
        assert set(body) == {"operation_id", "parameters"}
        assert body["parameters"] == [str(owner), needle]
        assert "select" not in str(body)
        assert request.headers["apikey"] == "sb_secret_test"
        assert "authorization" not in request.headers
        return httpx.Response(200, json=[])

    settings = Settings(
        _env_file=None, supabase_url="https://project.example", supabase_secret_key="sb_secret_test"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        repo = SupabaseRepository(settings, http)
        assert (
            await repo.many(
                "select * from messages where user_id=%s and content=%s", (owner, needle)
            )
            == []
        )


def test_decoding_keeps_report_json_untouched():
    owner = uuid4()
    row = decode_row(
        {
            "id": str(owner),
            "created_at": "2026-10-11T00:00:00Z",
            "report": {"id": "untrusted prose", "created_at": "yesterday"},
        }
    )
    assert row["id"] == owner
    assert row["created_at"] == datetime(2026, 10, 11, tzinfo=UTC)
    assert row["report"]["id"] == "untrusted prose"
    assert parameter([owner]) == '{"' + str(owner) + '"}'
    assert statement_id("select  id\nfrom topics") == statement_id("select id from topics")


async def test_database_error_response_is_sanitized():
    def handler(request):
        return httpx.Response(500, json={"message": "secret provider body"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        repo = SupabaseRepository(
            Settings(
                _env_file=None, supabase_secret_key="test", supabase_url="https://project.example"
            ),
            http,
        )
        with pytest.raises(HTTPException) as exc:
            await repo.command("claim", {})
        assert exc.value.status_code == 503
        assert "secret" not in exc.value.detail


async def test_chat_submission_uses_atomic_rpc_without_opening_client_transaction():
    owner = uuid4()
    job, conversation = uuid4(), uuid4()

    class Repo:
        async def command(self, operation, payload):
            assert operation == "submit_chat"
            assert payload["user_id"] == str(owner)
            return {"id": str(job), "conversation_id": str(conversation), "status": "queued"}

    result = await Jobs(Repo()).submit_chat(
        owner, ChatRequest(prompt="hello", idempotency_key=uuid4())
    )
    assert result["job_id"] == job
    assert result["conversation_id"] == conversation
