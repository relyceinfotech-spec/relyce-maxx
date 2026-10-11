# MAXXX

Backend implementation of the six brainstorms: two agents, shared research infrastructure,
Supabase authentication/Postgres, OpenRouter models, RSS/Serper discovery, and an evidence-backed
publishing workflow. There is no frontend in this workspace.

Start with [SETUP](docs/SETUP.md) for configuration and commands, and
[PROJECT_MAP](docs/PROJECT_MAP.md) to find where each feature belongs.

Current verification is local: `node tools/local-db/test.mjs` creates a disposable
PostgreSQL/pgvector database and runs the suite without hosted calls.
See [LOCAL_TESTING](docs/LOCAL_TESTING.md) for installation and coverage.
See [LIVE_CHAT](docs/LIVE_CHAT.md) for the current provider blocker and
[DECISIONS](docs/DECISIONS.md) for the disabled experimental gateway and comparison corpus.

```
backend/                  Python API, agents, services, workers and tests
  app/api/                HTTP contracts and authorization dependencies
  app/agents/chat/        Query routing and the research graph
  app/agents/post/        Evidence-backed draft graph
  app/research/           Evidence selection, quotes and citation validation
  app/tools/              Serper, safe fetching, HTML/PDF extraction
  app/discovery/          RSS ingestion, classification, canonical story clusters
  app/retrieval/          Owner-scoped keyword/vector knowledge retrieval
  app/services/           Job submission, controlled autonomous publishing, feed ranking
  app/database/           Connection pool, repository, migration command
  app/workers/            Durable job execution, scheduling and outbox delivery
  app/observability/      Model usage and tool execution records
  app/core/               Configuration, authentication, budgets, runtime
  tests/                  Offline tests and opt-in PostgreSQL integration tests
supabase/migrations/      Ordered schema, RLS, taxonomy and operation migrations
docs/                     Setup, API usage, architecture and verification status
tools/local-db/           Pinned embedded PostgreSQL test runner
.github/workflows/        Checks with a disposable PostgreSQL/pgvector service
```

## Run without credentials

Python 3.13 is the tested baseline. Python 3.15 is intentionally outside the package support
range until its dependency compatibility is validated.

```powershell
Set-Location 'D:\reserch maxxx\backend'
uv sync --frozen --extra dev --python 3.13
uv run maxxx-api
```

The API starts without keys. `GET /health` confirms the process is alive; `GET /ready` returns
503 and lists missing setting names. `http://127.0.0.1:8000/docs` exposes the interactive API
reference. The worker requires completed configuration and migrations.

## Configure later

Copy [backend/.env.example](backend/.env.example) to `backend/.env`, fill in your keys and model
IDs, run `uv run maxxx-setup`, then start `uv run maxxx-worker` in a second terminal. Full steps
and credential locations are in [SETUP](docs/SETUP.md). The ignored local environment has
already been populated from the authorized older checkout. The default [Supabase API runtime](docs/SUPABASE_API.md)
uses the existing secret/publishable keys; no database connection string is required.

## Implemented workflows

- Chat: normal answers, intelligent routing, owner-scoped conversation context, keyword RAG,
  optional semantic retrieval, web research, and bounded multi-round deep research.
- Evidence: retrieve real pages/PDFs, validate exact quotations and citation references,
  check entailment/contradictions with a separate model call, and withhold failed answers.
- Publishing: the autonomous Post Agent publishes through strict machine gates and a dedicated
  service identity. Revisions preserve history and visible update notes. See [publishing](docs/AUTONOMOUS_PUBLISHING.md).
- Discovery: curated RSS/Atom and Serper news queries, schedules, conditional requests,
  URL/publisher-ID deduplication and model-assisted event clustering. Optional automatic
  research proceeds through the autonomous publisher; search snippets never become verified evidence.
- Feed: normalized topic taxonomy, parent-topic interests, For You/Following/Latest modes,
  deterministic scoring, diversity and hide/block controls.
- Operations: persisted SSE timeline with replay, cancellation, explicit retries, private
  LangGraph checkpoints, leased jobs, durable follow-up outbox, cost reservations and traces.
- Updates: optional scheduled rechecks compare new verified research with the current article;
  meaningful changes create a revision checked by the autonomous publisher.

See [VALIDATION](docs/VALIDATION.md) for what has actually been checked and the remaining live
acceptance gates. This is an initial backend implementation, not a claim of production readiness.
