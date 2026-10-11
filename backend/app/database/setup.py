import asyncio
from pathlib import Path

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import Settings
from app.core.runtime import event_loop
from app.database.connection import create_pool
from app.database.supabase import SupabaseRepository

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def migration_files():
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(MIGRATIONS.glob("*.sql"))]


async def setup():
    settings = Settings()
    if settings.database_transport == "supabase":
        async with httpx.AsyncClient(timeout=20, trust_env=False) as http:
            repo = SupabaseRepository(settings, http)
            await repo.rpc(
                "maxxx_checkpoint",
                {"operation": "get", "payload": {"thread_id": "setup-schema-check", "ns": ""}},
            )
            topics = await repo.many(
                "select id,parent_id,slug,name from topics where is_active order by slug"
            )
            if not topics:
                raise RuntimeError(
                    "Apply Supabase migrations through the connected management integration"
                )
            print(
                f"Supabase API schema ready: {len(topics)} active topics; private checkpoint RPC available"
            )
            return
    pool = create_pool(settings)
    await pool.open(wait=True)
    try:
        async with pool.connection() as conn:
            await conn.execute("create schema if not exists agent_private")
            await conn.execute("revoke all on schema agent_private from public,anon,authenticated")
            await conn.execute("select pg_advisory_lock(73184133)")
            await conn.execute(
                "create table if not exists agent_private.schema_migrations (name text primary key)"
            )
            for name, sql in await asyncio.to_thread(migration_files):
                applied = await (
                    await conn.execute(
                        "select 1 from agent_private.schema_migrations where name=%s", (name,)
                    )
                ).fetchone()
                if not applied:
                    async with conn.transaction():
                        await conn.execute(sql)
                        await conn.execute(
                            "insert into agent_private.schema_migrations values(%s)", (name,)
                        )
                    print(f"Applied {name}")
            await conn.execute("set search_path to agent_private,public")
            await AsyncPostgresSaver(conn).setup()
            await conn.execute("reset search_path")
            await conn.execute("select pg_advisory_unlock(73184133)")
        print("Database and private graph checkpoints ready")
    finally:
        await pool.close()


def main():
    asyncio.run(setup(), loop_factory=event_loop)


if __name__ == "__main__":
    main()
