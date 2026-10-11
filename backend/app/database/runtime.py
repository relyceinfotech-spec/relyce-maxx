from contextlib import asynccontextmanager

from app.database.connection import create_pool
from app.database.repository import Repository
from app.database.supabase import SupabaseRepository


@asynccontextmanager
async def database(settings, http):
    if settings.database_transport == "supabase":
        yield SupabaseRepository(settings, http)
        return
    pool = create_pool(settings)
    try:
        await pool.open(wait=True, timeout=10)
        yield Repository(pool)
    finally:
        await pool.close()
