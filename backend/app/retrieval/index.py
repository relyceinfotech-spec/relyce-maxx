import argparse
import asyncio
from uuid import UUID

import httpx

from app.core.budget import Budget
from app.core.config import Settings
from app.core.errors import ConfigurationError
from app.core.runtime import event_loop
from app.database.runtime import database
from app.observability.tracing import Trace
from app.retrieval.knowledge import Knowledge


async def index(user_id, limit):
    settings = Settings()
    if not settings.model_embedding or not settings.openrouter_api_key.get_secret_value():
        raise ConfigurationError("Configure MODEL_EMBEDDING and OPENROUTER_API_KEY first")
    async with (
        httpx.AsyncClient(timeout=settings.http_timeout_seconds, trust_env=False) as http,
        database(settings, http) as repo,
    ):
        run = await repo.one(
            "insert into agent_runs(user_id,agent) values(%s,'index') returning id", (user_id,)
        )
        knowledge = Knowledge(repo, settings, http, Budget(settings), Trace(repo, run["id"]))
        try:
            async with asyncio.timeout(settings.run_timeout_seconds):
                await knowledge.index_pending(user_id, limit)
            await repo.execute(
                "update agent_runs set status='completed',finished_at=now() where id=%s",
                (run["id"],),
            )
        except Exception as exc:
            await repo.execute(
                "update agent_runs set status='failed',error_code=%s,finished_at=now() where id=%s",
                (type(exc).__name__, run["id"]),
            )
            raise


def main():
    parser = argparse.ArgumentParser(description="Index verified knowledge for one owning user")
    parser.add_argument("--user", required=True, type=UUID)
    parser.add_argument("--limit", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    asyncio.run(index(args.user, args.limit), loop_factory=event_loop)
