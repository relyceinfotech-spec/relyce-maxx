# Setup and operations

For current development checks, use [local database testing](LOCAL_TESTING.md).
It supplies an isolated database and offline providers; no Supabase credentials or
deployment are needed. The settings below describe the hosted runtime.

## 1. Install

From the repository root:

```powershell
Set-Location backend
uv sync --frozen --extra dev --python 3.13
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Fill the real file locally. It is ignored by Git. Configuration always loads `backend/.env`,
independent of the working directory. Process environment variables take precedence.

The existing local `backend/.env` has already been populated from the authorized older
checkout. Keep it when installing dependencies. Python 3.13.7 is the verified runtime.
The default uses the supplied Supabase Data API credentials and does not require
`DATABASE_URL`. The legacy schema was replaced and protected runtime RPCs are deployed.
See [Supabase API runtime](SUPABASE_API.md) for the adapter and migration workflow.

| Setting | Where to get it / what it means |
| --- | --- |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_PUBLISHABLE_KEY` | Supabase publishable/anon key for calling Auth; not the service-role key |
| `SUPABASE_SECRET_KEY` | Backend-only secret/service-role key for protected database RPCs |
| `DATABASE_TRANSPORT` | `supabase` by default; optional `postgres` for direct connections |
| `DATABASE_URL` | Only required in `postgres` mode; direct/session-pool URL |
| `OPENROUTER_API_KEY` | OpenRouter API key, backend only |
| `MODEL_CHAT` | Balanced model for direct chat |
| `MODEL_FAST` | Structured-output model for routing/classification/update assessment |
| `MODEL_RESEARCH` | Structured-output model for plans, synthesis, verification and drafts |
| `MODEL_FALLBACK` | Optional supported model; one fallback only on 429/502/503/504 |
| `SERPER_API_KEY` | Serper web/news search API key |
| `EDITOR_USER_IDS` | JSON array of actual Supabase Auth user UUIDs allowed to administer/review |
| `CORS_ORIGINS` | JSON array of future frontend origins; empty until you have a frontend |
| `MODEL_EMBEDDING` | Optional embedding model; leave empty for keyword retrieval |

No model IDs are guessed. Choose models supporting the parameters/JSON schemas they will use.
The code uses OpenRouter's [structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
and still validates results with Pydantic.

The backend uses privileged service-only RPCs for workers. Its services explicitly enforce
owner checks because the server key can bypass RLS. Client-side access is
restricted separately by Supabase [RLS](https://supabase.com/docs/guides/database/postgres/row-level-security).
Never expose the database URL or OpenRouter/Serper keys in a frontend.

## 2. Verify the configured Supabase database

```powershell
uv run maxxx-setup
```

In default API mode this verifies deployed RPCs and taxonomy. Install new migrations
through the connected Supabase management integration. The current project is configured.

In optional `postgres` mode this applies numbered migrations atomically, records them in `agent_private.schema_migrations`,
seeds the topic taxonomy and initializes LangGraph's private PostgreSQL checkpoints. It does not
seed fake users, posts, research or conversations. Apply to a new Supabase project; existing
projects need their schema reconciled first. Do not manually paste migrations and then run setup
without also maintaining the migration registry.

The default graph checkpointer uses protected Supabase RPCs. The optional direct graph
checkpointer uses the direct/session connection, autocommit and dictionary rows.
It follows the required [Postgres checkpointer setup](https://reference.langchain.com/python/langgraph.checkpoint.postgres/aio/AsyncPostgresSaver/setup).
Use the supplied `maxxx-api`, `maxxx-worker` and `maxxx-setup` commands on Windows: they select
a compatible event loop for [Psycopg async](https://www.psycopg.org/psycopg3/docs/advanced/async.html).

## 3. Start API and worker

```powershell
# Terminal one, from backend/
uv run maxxx-api

# Terminal two, from backend/
uv run maxxx-worker
```

The API accepts jobs; the separate worker actually executes them. `/health` checks the process.
`/ready` checks required setting names and the research schema, not live model/search quality or
worker acceptance. `/api/admin/operations` shows queue/outbox counts and worker heartbeats.
Heartbeats can be up to one run timeout apart while a worker is busy.

Create/sign in a user with Supabase Auth, then use the session access token as
`Authorization: Bearer <token>` in `/docs` or an API client. There is no development authentication bypass.

## 4. Enable discovery when ready

Register real sources with `/api/admin/sources`, and curated searches with `/api/admin/queries`.
Both are disabled by default. Re-post the same source URL or topic/query pair to update its
configuration. Inspect source health and robots/site terms before enabling a source.

Set `DISCOVERY_ENABLED=true` to poll due registries. Up to three RSS sources and two Serper
queries are reserved per cycle. Schedules are reserved with database locks across workers.
`PUBLISHER_USER_ID` owns system-generated research independently of editor accounts.

Use `/api/admin/clusters/{id}/research` to investigate a selected cluster. It queues research,
then automatically queues a post draft if the report passes validation. Set
`DISCOVERY_AUTO_RESEARCH=true` to also queue at most one newly discovered cluster each cycle.
Autonomous publication requires no editorial approval. Provisioning, trusted-source policy,
credential scope and limits are described in [AUTONOMOUS_PUBLISHING](AUTONOMOUS_PUBLISHING.md).
The new migration is currently local and must be deployed before enabling this workflow.

Set `RECHECK_ENABLED=true` to recheck at most one eligible published post per cycle, with a
minimum age controlled by `RECHECK_MIN_AGE_HOURS`. A verified material change becomes a
revision with an update note and passes the autonomous publishing gates. Unchanged results do not create revisions. This is a bounded
initial scheduler, not a high-volume scheduling platform.

## 5. Budgets and recovery

Tune page/search limits, `MAX_TOTAL_TOKENS`, `MAX_MODEL_CALLS`, `RUN_TIMEOUT_SECONDS`, and
`MAX_RUN_COST_USD`. Set `MODEL_PRICE_CEILING_PER_MILLION` at or above the highest input/output
per-token price across every configured model, including fallback and embeddings. Reservations
use conservative UTF-8 byte estimates, not an exact tokenizer. Actual cost is recorded when
the provider returns it. Serper charges are controlled by search counts, not the model USD budget.

Limits may stop a job rather than return an unverified conclusion. Research source passages
are compacted before model calls. Deep research currently has at most two evidence rounds,
with both rounds sharing the configured budget. Increase budgets intentionally after inspecting traces.

Workers use leases longer than the run deadline, with fencing checks before paid calls and
final writes. Expired running jobs resume saved graph state; counters are reserved durably
before calls. External provider calls have at-least-once semantics: a crash between a paid call
and its graph checkpoint can repeat that call on recovery, within the remaining reservation
budget. No exactly-once billing claim is made. A failed job stays failed until its owner explicitly
retries it; retry creates a new checkpoint generation and a fresh budget.

Cancellation revokes the lease. An in-flight HTTP request may finish, but it cannot commit a
completed report or draft after cancellation. Completed research-to-draft follow-ups use a
transactional outbox and idempotent delivery. Failed outbox deliveries are visible in operations;
request `/api/posts/from-research` manually to recover a completed report.

## 6. Optional semantic knowledge

Verified research is saved into owner-scoped chunks. Keyword retrieval works immediately.
After configuring a 1536-dimensional embedding model, index one user's saved chunks:

```powershell
uv run maxxx-index --user USER_UUID --limit 5
```

Then knowledge retrieval combines keyword matches with semantic neighbors. Indexing is explicit
and bounded; arbitrary external documents and unverified snippets are not automatically embedded.
Changing embedding dimensions requires a database migration before changing configuration.

## 7. Tests and containers

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest -q -p pytest_asyncio.plugin
uv run python scripts/smoke_api.py
```

If unrelated installed pytest plugins interfere, set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` first.
Offline tests never call paid providers. Real database tests require `TEST_DATABASE_URL` pointing
to a **fresh disposable** database named exactly `maxxx_test`, with pgvector available. They
bootstrap a minimal Auth schema only for testing. The GitHub workflow supplies that database.
Do not point these tests at a Supabase project with real data.

From the root, `docker compose up --build api` starts only the API; after configuration and
setup, `docker compose --profile workers up --build` starts both processes. Docker artifacts are
provided but have not been built on this machine. Keep schema setup as an explicit release step.
