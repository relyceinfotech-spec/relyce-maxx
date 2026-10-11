# Legacy database findings

Current status (2026-10-11): Supabase access is connected. Catalog verification confirms
legacy schemas are gone and all three new application migrations are installed.
The replacement was already present, so it was not rerun. This session then installed
and verified the private graph checkpoints through the Supabase integration.
Historical discovery and preparation details follow; `DATABASE_URL` remains required
for running the backend API and workers.

Checked 2026-10-11 against the project configured in the authorized older checkout.
No secret values or table rows were displayed or copied. No schema was changed.

## Connections inspected

- Older root `.env`: Supabase project URL, publishable/secret API keys, OpenRouter and Serper.
  Needed Auth/model/search settings are already in the new ignored `backend/.env`.
- Older `apps/api/.env`: OpenRouter and search settings; no database connection.
- Older `older agent/backend/.env`: Firebase, Weaviate, Redis and payment connections.
  These do not match the new Supabase architecture and were not imported.
- Source/configuration files: the old TypeScript backend uses `@supabase/supabase-js`
  with schema-specific Data API requests. No PostgreSQL connection string, database
  password, Supabase management token or general SQL execution RPC was found.

The older `docs/production-security-assurance-v1.md` records remote migrations through
Supabase MCP `apply_migration`, plus Supabase CLI migration-ledger repairs. This is
evidence of a separate previously connected management integration, not SQL execution
through the TypeScript project's API key. The old package scripts contain no hosted
migration runner. `scripts/verify-backend-integrity.mjs` executes SQL in local PGlite
and explicitly never connects to Supabase.

The Supabase integration is available in the current Codex plugin catalog, but the
current session's discovery reports `installed: false`; no Supabase SQL tool is exposed.
Installing and connecting that integration would provide a route to execute the prepared
replacement SQL without asking the user to paste database secrets into this chat.

## Confirmed exposed legacy tables

Read-only REST OpenAPI requests with `Accept-Profile` returned these table names:

| Schema | Tables |
| --- | --- |
| `research` | `max_conversation_messages`, `max_conversations`, `max_deleted_research_sessions`, `max_jobs`, `max_knowledge_documents`, `max_request_rate_limits`, `max_research_sessions`, `max_user_memories`, `max_user_quota_windows` |
| `content` | `max_autonomous_runs`, `max_exports`, `max_post_claims`, `max_post_followups`, `max_post_sources`, `max_posts`, `max_shares`, `max_topics` |
| `billing` | `customer_accounts`, `plans`, `subscriptions`, `webhook_events` |

`public` exposed no tables. This explains why the first public-only probe missed the
legacy application tables. API metadata is an inventory of exposed objects, not a
complete PostgreSQL catalog. Hidden tables, dependencies and Auth triggers still need
database inspection before cleanup.

## Replacement status

The new schema is defined in `supabase/migrations/001_core.sql`, `002_taxonomy.sql`
and `003_operations.sql`, with private checkpoints initialized by `maxxx-setup`.
It uses direct PostgreSQL transactions and does not reuse the legacy REST RPC contract.

Legacy cleanup is authorized by the user but has not run. Once `DATABASE_URL` is
available, inspect catalog dependencies, remove the obsolete application objects,
preserve Supabase-managed Auth/Storage/extensions, and apply the new migrations.
Do not run `maxxx-setup` as a substitute for legacy cleanup.

## Prepared SQL Editor replacement

`supabase/replace_legacy.sql` is a standalone generated script for the project's SQL Editor.
It removes the 21 identified legacy tables, their known functions and now-empty legacy
schemas using `RESTRICT`, then installs the three current migrations and their registry
records in the same transaction. It refuses an existing v2 installation. Unknown dependencies
or remaining schema objects cause failure; roll back and inspect them rather than adding CASCADE.
Auth, Storage and extension schemas are not targeted. Stop legacy workers before running it.

Regenerate after changing the migration files:

```powershell
backend/.venv/Scripts/python.exe backend/scripts/build_replacement_sql.py
```

Run the **whole** generated file in the intended project's SQL Editor. It has not been
executed remotely by this session. After success, configure `DATABASE_URL` and run
`maxxx-setup` from `backend/` to initialize private LangGraph checkpoints; the application
still requires that connection for its API and worker transactions.
