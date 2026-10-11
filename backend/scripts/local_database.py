"""Bootstrap only a fresh disposable maxxx_test database, never application .env."""

from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict


def initialize_database(url):
    config = conninfo_to_dict(url)
    if config.get("dbname") != "maxxx_test" or config.get("host") not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        raise RuntimeError("Refusing anything except a local disposable maxxx_test database")
    with psycopg.connect(url, autocommit=True) as conn:
        count = conn.execute("select count(*) from pg_tables where schemaname='public'").fetchone()[
            0
        ]
        if count:
            raise RuntimeError("Disposable database must have no public tables")
        conn.execute("create schema auth; create schema extensions; create schema agent_private")
        conn.execute("create table auth.users(id uuid primary key)")
        conn.execute(
            "create role anon; create role authenticated; create role service_role bypassrls; create role authenticator"
        )
        conn.execute("create table agent_private.schema_migrations(name text primary key)")
        conn.execute(
            "create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$"
        )
        conn.execute("grant usage on schema auth to anon,authenticated")
        root = Path(__file__).resolve().parents[2] / "supabase"
        for file in sorted((root / "migrations").glob("*.sql")):
            with conn.transaction():
                conn.execute(file.read_text(encoding="utf-8"))
        with conn.transaction():
            conn.execute((root / "checkpoints_setup.sql").read_text(encoding="utf-8"))
