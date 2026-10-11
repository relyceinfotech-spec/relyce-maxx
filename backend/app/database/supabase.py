"""Supabase Data API adapter; only deployed operation IDs cross the network."""

import hashlib
import json
from datetime import date, datetime
from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.core.errors import ConfigurationError, LeaseLost


def statement_id(sql):
    return hashlib.sha256(" ".join(sql.split()).encode()).hexdigest()


def parameter(value):
    if isinstance(value, Jsonb):
        return json.dumps(value.obj, default=str)
    if isinstance(value, (UUID, datetime, date)):
        return str(value)
    if isinstance(value, (tuple, list)):
        return "{" + ",".join("NULL" if v is None else json.dumps(str(v)) for v in value) + "}"
    return value


UUID_FIELDS = {
    "id",
    "user_id",
    "conversation_id",
    "post_id",
    "version_id",
    "current_version_id",
    "previous_version_id",
    "research_job_id",
    "draft_job_id",
    "cluster_id",
    "source_id",
    "topic_id",
    "parent_id",
    "created_by",
    "reviewer_id",
    "lease_id",
    "idempotency_key",
    "run_id",
    "job_id",
    "query_id",
    "worker_id",
}
TIME_FIELDS = {
    "created_at",
    "published_at",
    "finished_at",
    "started_at",
    "first_seen_at",
    "leased_until",
    "last_seen_at",
    "last_polled_at",
    "last_success_at",
    "next_poll_at",
}


def decode_row(row):
    # Decode database columns only. User/report JSON remains unmodified.
    result = dict(row)
    for key, value in row.items():
        if value is None:
            continue
        if key in UUID_FIELDS and isinstance(value, str):
            result[key] = UUID(value)
        elif key in TIME_FIELDS and isinstance(value, str):
            result[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif key == "topic_ids":
            result[key] = [UUID(v) for v in value]
    return result


class SupabaseRepository:
    def __init__(self, settings, http):
        self.settings, self.http = settings, http
        self.lease_seconds = settings.job_lease_seconds

    async def rpc(self, operation, payload):
        key = self.settings.supabase_secret_key.get_secret_value()
        if not key:
            raise ConfigurationError("SUPABASE_SECRET_KEY is not configured")
        headers = {"apikey": key, "Content-Profile": "public", "Accept-Profile": "public"}
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        response = await self.http.post(
            self.settings.supabase_url.rstrip("/") + "/rest/v1/rpc/" + operation,
            headers=headers,
            json=payload,
        )
        if response.is_error:
            # Do not return or log provider bodies, credentials or private SQL parameters.
            try:
                code = response.json().get("code")
            except (ValueError, AttributeError):
                code = None
            if code == "23505":
                raise HTTPException(409, "Concurrent or duplicate state conflict")
            if response.status_code in (404, 409, 422):
                raise HTTPException(response.status_code, "Database operation rejected")
            raise HTTPException(503, "Supabase database operation unavailable")
        return response.json()

    async def command(self, operation, payload):
        return await self.rpc("maxxx_command", {"operation": operation, "payload": payload})

    async def many(self, sql, params=()):
        rows = await self.rpc(
            "maxxx_query",
            {"operation_id": statement_id(sql), "parameters": [parameter(v) for v in params]},
        )
        return [decode_row(row) for row in rows]

    async def one(self, sql, params=()):
        rows = await self.many(sql, params)
        return rows[0] if rows else None

    async def execute(self, sql, params=()):
        await self.many(sql, params)

    async def owned_job(self, job_id, user_id):
        return await self.one(
            "select * from research_jobs where id=%s and user_id=%s", (job_id, user_id)
        )

    async def claim(self):
        row = await self.command("claim", {"lease_seconds": self.lease_seconds})
        return decode_row(row) if row else None

    async def event(self, job, stage, data):
        row = await self.one(
            """insert into research_events(job_id,user_id,stage,data)
            select id,user_id,%s,%s from research_jobs
            where id=%s and status='running' and lease_id=%s and leased_until>now()
            returning id""",
            (stage, Jsonb(data), job["id"], job["lease_id"]),
        )
        if not row:
            raise LeaseLost("Job cancelled or lease expired")

    async def finish(self, job, report, run_id, follow_up=None):
        result = await self.command(
            "finish",
            {
                "job_id": str(job["id"]),
                "lease_id": str(job["lease_id"]),
                "run_id": str(run_id),
                "report": report,
                "follow_up": follow_up,
            },
        )
        if not result:
            raise LeaseLost("Job cancelled or lease expired")

    async def fail(self, job, run_id, code):
        await self.command(
            "fail",
            {
                "job_id": str(job["id"]),
                "lease_id": str(job["lease_id"]),
                "run_id": str(run_id),
                "code": code,
            },
        )
