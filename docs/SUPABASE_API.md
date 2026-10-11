# Supabase API runtime

The default is `DATABASE_TRANSPORT=supabase`. The backend uses the existing project URL,
publishable Auth key and backend-only secret/service-role key. `DATABASE_URL` is optional.
Your original environment was left unchanged; the needed values are in ignored `backend/.env`.

## Code map

| File | Responsibility |
| --- | --- |
| `backend/app/database/supabase.py` | Data API calls, parameter encoding, typed columns and sanitized errors |
| `backend/app/database/runtime.py` | Repository lifetime and transport selection |
| `backend/app/database/checkpoints.py` | Durable LangGraph state, pending writes and restart recovery over RPC |
| `supabase/runtime_functions.sql` | Current source for atomic Chat, queue, completion, draft, review, interests and discovery operations |
| `supabase/runtime_checkpoints.sql` | Current source for private HTTP checkpoint tables/functions |
| `backend/scripts/build_runtime_catalog.py` | Compile literal application statements into a fixed operation allowlist |
| `supabase/migrations/` | Append-only applied schema history |

The API verifies each user's token with Supabase Auth. Services then use that verified
owner UUID in queries and commands. The server key remains privileged, so explicit
ownership and editor checks are required. Never expose it to frontend clients.

The public RPC wrappers are `SECURITY INVOKER` and executable only by `service_role`.
Privileged implementations are in `agent_private`, with pinned search paths and revoked
PUBLIC/anon/authenticated execute privileges. Private tables have RLS and no client policies.
Data API schemas expose `public,graphql_public`; stale legacy entries were removed.

Simple queries use a fixed SHA-256 operation ID resolved against a server-owned catalog.
Requests send the ID and parameter values, never SQL text. The server quotes values and
rejects unknown IDs or wrong argument counts. Arrays/JSON are encoded as literal values.
Operations involving dependent writes execute in a single named RPC transaction, including
Chat submission, completion/outbox insertion, draft persistence and publication/review.
Separate HTTP requests do not pretend to share a PostgreSQL transaction.

## Updating database operations

Run `backend/.venv/Scripts/python.exe backend/scripts/build_runtime_catalog.py` after changing
literal application statements. Use `supabase migration new <name>` to create a migration,
include the generated catalog and changed functions, and deploy through management access.
Do not edit already applied migration files. Runtime API keys do not install arbitrary DDL.

## Checks

From `backend/`:

```powershell
uv run maxxx-setup
uv run python scripts/smoke_supabase.py
```

In API mode setup verifies deployed RPCs and taxonomy. The smoke verifies real readiness,
topic reads, persisted graph interruption/resumption and cleanup. It makes no paid calls.

`scripts/smoke_chat_post.py` is a separate deliberate acceptance tool. It creates two temporary
Auth users and stores only cleanup IDs in ignored `.local-state/smoke.json`; no passwords or
tokens are persisted. It never approves or publishes content. `--deterministic` substitutes
model/search responses while Auth, database, worker persistence and official-page fetching
remain real. This does not establish real-model evidence quality. Remove the identified
records/accounts through management access after each run and retain IDs until cleanup is verified.

`DATABASE_TRANSPORT=postgres` remains an optional direct adapter for disposable integration
tests or deployments that explicitly configure a PostgreSQL connection.
