import logging
from contextlib import asynccontextmanager

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from app.api import admin, chat, feed, posts, research
from app.core.config import Settings
from app.core.errors import EvidenceError, FetchRejected
from app.database.connection import create_pool
from app.database.repository import Repository
from app.database.supabase import SupabaseRepository


def create_app(settings: Settings | None = None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings
        app.state.repo = None
        pool = None
        async with httpx.AsyncClient(
            timeout=settings.http_timeout_seconds, trust_env=False
        ) as http:
            app.state.http = http
            if (
                settings.database_transport == "supabase"
                and settings.supabase_secret_key.get_secret_value()
            ):
                app.state.repo = SupabaseRepository(settings, http)
            elif (
                settings.database_transport == "postgres"
                and settings.database_url.get_secret_value()
            ):
                pool = create_pool(settings)
                try:
                    await pool.open(wait=True, timeout=10)
                    app.state.repo = Repository(pool)
                except Exception as exc:
                    logging.getLogger("maxxx.api").error(
                        "database_unavailable category=%s", type(exc).__name__
                    )
            yield
        if pool:
            await pool.close()

    app = FastAPI(
        title="MAXXX Backend",
        version="0.1.0",
        lifespan=lifespan,
        description="Research, evidence, discovery and approval-gated publishing",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.exception_handler(psycopg.errors.UniqueViolation)
    async def state_conflict(request: Request, exc):
        return JSONResponse(
            {"detail": "Concurrent or duplicate state conflict; refresh the resource"}, 409
        )

    @app.exception_handler(psycopg.Error)
    @app.exception_handler(PoolTimeout)
    async def database_error(request: Request, exc):
        logging.getLogger("maxxx.api").error(
            "database_request_failed category=%s", type(exc).__name__
        )
        return JSONResponse(
            {"detail": "Database operation unavailable; check migrations and configuration"}, 503
        )

    @app.exception_handler(FetchRejected)
    @app.exception_handler(EvidenceError)
    async def validation_error(request: Request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.get("/health", tags=["Operations"])
    async def health():
        return {"status": "ok", "service": "maxxx-backend"}

    @app.get("/ready", tags=["Operations"])
    async def ready(request: Request):
        missing = settings.missing()
        db_ready = False
        if request.app.state.repo:
            try:
                row = await request.app.state.repo.one(
                    """select to_regclass('public.research_jobs') is not null
                    and to_regclass('agent_private.schema_migrations') is not null
                    and to_regclass('agent_private.checkpoint_migrations') is not null
                    and to_regclass('agent_private.checkpoints') is not null
                    and to_regclass('agent_private.checkpoint_blobs') is not null
                    and to_regclass('agent_private.checkpoint_writes') is not null
                    and to_regclass('agent_private.http_checkpoints') is not null
                    and to_regclass('agent_private.operation_catalog') is not null as ready"""
                )
                db_ready = row["ready"]
            except (psycopg.Error, PoolTimeout, httpx.HTTPError, HTTPException):
                pass
        return JSONResponse(
            {
                "status": "ready" if not missing and db_ready else "not_ready",
                "missing_configuration": missing,
                "database_schema_ready": db_ready,
            },
            200 if not missing and db_ready else 503,
        )

    for router in (chat.router, research.router, posts.router, feed.router, admin.router):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
