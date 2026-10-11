"""Controlled publisher, never a model tool or a Chat capability."""

import re
import time

from psycopg.types.json import Jsonb

from app.core.errors import ConfigurationError, EvidenceError


def content_checks(draft, report):
    text = "\n".join((draft.title, draft.summary, draft.body))
    if re.search(
        r"<\s*(script|iframe|object)|javascript:|data:text/html|\bsk-(?:or-)?[\w-]{20,}", text, re.I
    ):
        raise EvidenceError("Unsafe publication content")
    # Long verbatim passages cannot be presented as an original article.
    normalized = " ".join(text.casefold().split())
    for source in report.sources:
        words = " ".join(source.text.casefold().split())
        if len(words) >= 500 and any(
            words[i : i + 500] in normalized for i in range(0, len(words) - 499, 100)
        ):
            raise EvidenceError("Publication copies a source passage")
    return {"content_safe": True, "evidence_checked": True}


class AutonomousPublisher:
    """A dedicated Auth identity can only invoke the restricted publication RPC.

    Service-role credentials are deliberately absent from this client. Database
    checks remain authoritative even when a caller supplies a forged payload.
    """

    def __init__(self, settings, http):
        self.settings, self.http = settings, http
        self.access = settings.publisher_access_token.get_secret_value()
        self.refresh = settings.publisher_refresh_token.get_secret_value()
        self.expires_at = 0  # Refresh configured sessions before the first publish.

    async def publish(self, job, version_id):
        if (
            not self.settings.autonomous_publishing_enabled
            or job["user_id"] != self.settings.publisher_user_id
        ):
            raise ConfigurationError("Autonomous publishing identity is not enabled")
        if time.monotonic() >= self.expires_at and (self.refresh or not self.access):
            await self.authenticate()
        if not self.access:
            raise ConfigurationError("Publisher Auth session is missing")
        response = await self.http.post(
            self.settings.supabase_url.rstrip("/") + "/rest/v1/rpc/maxxx_autopublish",
            headers={
                "apikey": self.settings.supabase_publishable_key.get_secret_value(),
                "Authorization": "Bearer " + self.access,
            },
            json={
                "p_job_id": str(job["id"]),
                "p_lease_id": str(job["lease_id"]),
                "p_version_id": str(version_id),
            },
        )
        response.raise_for_status()
        return response.json()

    async def authenticate(self):
        password = self.settings.publisher_password.get_secret_value()
        session = None
        if self.refresh:
            response = await self.http.post(
                self.settings.supabase_url.rstrip("/") + "/auth/v1/token",
                params={"grant_type": "refresh_token"},
                headers={"apikey": self.settings.supabase_publishable_key.get_secret_value()},
                json={"refresh_token": self.refresh},
            )
            if response.status_code in (400, 401) and password and self.settings.publisher_email:
                self.refresh = ""
            else:
                response.raise_for_status()
                session = response.json()
        if session is None:
            if not password or not self.settings.publisher_email:
                raise ConfigurationError("Publisher refresh token or account credentials required")
            response = await self.http.post(
                self.settings.supabase_url.rstrip("/") + "/auth/v1/token",
                params={"grant_type": "password"},
                headers={"apikey": self.settings.supabase_publishable_key.get_secret_value()},
                json={"email": self.settings.publisher_email, "password": password},
            )
            response.raise_for_status()
            session = response.json()
        self.access = session["access_token"]
        self.refresh = session.get("refresh_token", self.refresh)
        self.expires_at = time.monotonic() + max(0, session["expires_in"] - 60)


async def record_checks(repo, version_id, run_id, draft, report):
    checks = content_checks(draft, report)
    await repo.execute(
        """insert into agent_private.publication_checks(version_id,run_id,passed,checks)
        values(%s,%s,true,%s) on conflict(version_id) do update
        set run_id=excluded.run_id,passed=excluded.passed,checks=excluded.checks""",
        (version_id, run_id, Jsonb(checks)),
    )
