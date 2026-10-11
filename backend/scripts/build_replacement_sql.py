"""Build the standalone, transactional SQL Editor replacement artifact."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build() -> Path:
    directory = ROOT / "supabase"
    parts = [
        "-- DESTRUCTIVE: removes legacy MAX data and installs MAXXX v2.\n"
        "-- Run the complete file in the intended project's Supabase SQL Editor.\n"
        "-- Stop legacy workers before executing. Auth, Storage and extensions are preserved.\n"
        "-- Any error must ROLLBACK; do not execute selected statements after an error.\n"
        "begin;\n"
        "set local lock_timeout = '10s';\n"
        "set local statement_timeout = '120s';\n"
        "select pg_advisory_xact_lock(73184133);\n"
        "-- Refuse reruns and existing v2 installs before deleting anything.\n"
        "do $guard$ begin\n"
        " if to_regclass('public.research_jobs') is not null\n"
        " or to_regclass('agent_private.schema_migrations') is not null then\n"
        "  raise exception 'Existing MAXXX v2 installation: replacement refused';\n"
        " end if;\n"
        "end $guard$;\n",
        (directory / "legacy_cleanup.sql").read_text(encoding="utf-8"),
    ]
    migrations = sorted((directory / "migrations").glob("*.sql"))
    for migration in migrations:
        parts.append(f"\n-- BEGIN {migration.name}\n")
        parts.append(migration.read_text(encoding="utf-8"))
    parts.append(
        "\ncreate table agent_private.schema_migrations (name text primary key);\n"
        "revoke all on agent_private.schema_migrations from public, anon, authenticated;\n"
    )
    for migration in migrations:
        name = migration.name.replace("'", "''")
        parts.append(f"insert into agent_private.schema_migrations values ('{name}');\n")
    parts.append(
        "notify pgrst, 'reload schema';\n"
        "commit;\n"
        "-- Next run maxxx-setup from backend/ to initialize private LangGraph checkpoints.\n"
    )
    output = directory / "replace_legacy.sql"
    output.write_text("\n".join(parts), encoding="utf-8")
    return output


if __name__ == "__main__":
    print(build())
