import asyncio
import os
import socket
import sys
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest

from app.core.budget import Budget
from app.core.config import Settings
from app.database.connection import create_pool
from app.database.repository import Repository


@pytest.hookimpl(optionalhook=True)
def pytest_asyncio_loop_factories(config, item):
    return {
        "database": asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    }


@pytest.fixture(autouse=True)
def forbid_external_network_in_local_run(monkeypatch):
    if os.environ.get("MAXXX_LOCAL_TESTS") != "1":
        return
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def check(address):
        if isinstance(address, tuple):
            assert address[0] in ("127.0.0.1", "::1", "localhost"), (
                "External network forbidden in local tests"
            )

    def guarded_connect(sock, address):
        check(address)
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        check(address)
        return connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture
def settings():
    return Settings(_env_file=None, max_run_cost_usd=100, max_total_tokens=200000)


class FakeRepo:
    def __init__(self):
        self.statements = []

    async def one(self, sql, params=()):
        self.statements.append((sql, params))
        return None

    async def many(self, sql, params=()):
        self.statements.append((sql, params))
        return []

    async def execute(self, sql, params=()):
        self.statements.append((sql, params))


class FakeTrace:
    def __init__(self):
        self.calls = []

    async def reserve(self, budget):
        pass

    async def model(self, *args):
        self.calls.append(args)

    def tool(self, name):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def run():
            self.calls.append(name)
            yield

        return run()


def context(settings, llm, search=None, fetcher=None, knowledge=None):
    events = []

    async def event(stage, data):
        events.append((stage, data))

    return SimpleNamespace(
        settings=settings,
        budget=Budget(settings),
        repo=FakeRepo(),
        llm=llm,
        job={"mode": "auto", "user_id": uuid4(), "conversation_id": None, "post_id": None},
        search=search,
        fetcher=fetcher,
        knowledge=knowledge,
        trace=FakeTrace(),
        event=event,
        events=events,
    )


URL = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(scope="session")
def database_url():
    if not URL:
        pytest.skip("TEST_DATABASE_URL not set; real database verification pending")
    from scripts.local_database import initialize_database

    initialize_database(URL)
    return URL


@pytest.fixture
async def repo(database_url):
    from pydantic import SecretStr

    if os.environ.get("MAXXX_LOCAL_TESTS") == "1":
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        # Embedded PostgreSQL uses one session and the simple wire protocol.
        pool = AsyncConnectionPool(
            database_url,
            open=False,
            min_size=1,
            max_size=1,
            kwargs={
                "row_factory": dict_row,
                "autocommit": True,
                "cursor_factory": psycopg.AsyncClientCursor,
            },
        )
    else:
        pool = create_pool(Settings(_env_file=None, database_url=SecretStr(database_url)))
    await pool.open(wait=True)
    repository = Repository(pool)
    repository.lease_seconds = 360
    yield repository
    await pool.close()
