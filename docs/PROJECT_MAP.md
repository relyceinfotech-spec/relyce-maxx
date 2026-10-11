# Where code belongs

| Change you want | Location | Responsibility |
| --- | --- | --- |
| Environment setting or default | `backend/app/core/config.py` + `.env.example` | Validate once; never read environment ad hoc inside agents |
| New request/response structure | `backend/app/domain/models.py` | Pydantic schemas; no database/network calls |
| HTTP endpoint | `backend/app/api/` | Parse, authenticate, call a service; keep business logic out |
| Who can do what | `backend/app/core/auth.py` | Supabase Auth verification and editor allowlist |
| Normal vs web vs deep decisions | `backend/app/agents/chat/router.py` | Explicit requests, freshness rules and model classifier |
| Research workflow changes | `backend/app/agents/chat/graph.py` | Conditional orchestration; no new agent per mode |
| Post generation changes | `backend/app/agents/post/graph.py` | Generate and validate; never publish directly |
| Provider payload/fallback behavior | `backend/app/llm/openrouter.py` | One gateway; validated structured output |
| Structural answer completeness | `backend/app/llm/completeness.py` | Reject open code fences and obvious dangling sentence/citation endings |
| Research evaluation evidence audit | `backend/app/research/audit.py` + `backend/scripts/audit_chat_evidence.py` | Map captured passages to claims and citations; keep semantic review distinct |
| Source priority and document dates | `backend/app/research/selection.py` | Order useful candidates before social pages; exclude historical content for current-week requests |
| Numbered synthesis passages | `backend/app/research/evidence.py` | Stable source/passages, verbatim spans and dated independent verification payloads |
| Read-only credit preflight | `backend/app/llm/credits.py` | Known key/org limits before Chat search; unknown account balance stays unknown |
| Conversation sizing | `backend/app/agents/chat/context.py` + `backend/app/core/text.py` | Bound recent history, label overflow excerpts and preserve Unicode |
| Experimental decision models | `backend/app/llm/decisions.py` | Shared choice adapter; shadow predictions cannot authorize tools or publish |
| Search provider | `backend/app/tools/serper.py` | Search results are candidates, not evidence |
| Job-scoped webpage navigation | `backend/app/tools/reader.py` | Cached public pages, exact passage offsets, find and bounded nearby reading |
| URL/security/robots handling | `backend/app/tools/fetcher.py` | Public DNS/IP pinning, redirects, limits and robots |
| HTML/PDF extraction | `backend/app/tools/extractor.py` | Convert permitted bytes to bounded readable text |
| Evidence/citations | `backend/app/research/` | Selected passages, exact quotes and server-created links |
| Answer coverage and issue references | `backend/app/research/coverage.py` | Sentence/claim mappings, deterministic markers, safe indexed diagnostics and verifier-reference checks |
| Footer-only extraction checks | `backend/app/research/content.py` | Conservative navigation/legal-only rejection before caching and Chat synthesis |
| RSS ingestion and clustering | `backend/app/discovery/engine.py` | Canonical discoveries, topic classification, deduplication |
| Stored knowledge and embeddings | `backend/app/retrieval/` | Owner-scoped retrieval and optional indexing |
| Queueing/cancellation/retry | `backend/app/services/jobs.py` | Transactions, ownership and idempotency |
| Draft persistence and legacy reviews | `backend/app/services/publishing.py` | Atomic drafts; legacy review cannot publish autonomous drafts |
| Autonomous publication | `backend/app/services/autonomous.py` + latest SQL migration | Dedicated identity, evidence gates, reservations and atomic version publication |
| Decision comparisons | `backend/evals/` + `backend/scripts/eval_chat_routes.py` | 100 Chat / 100 real discovery / 50 difficult cases; explicit live opt-in |
| Real Chat provider acceptance | `backend/scripts/live_chat_smoke.py` | Actual providers with disposable local database and no publishing |
| Personalized ranking | `backend/app/services/feed.py` | Explainable deterministic ranking, never an LLM per request |
| Background execution | `backend/app/workers/runner.py` | Lease fencing, graph recovery, deadlines and job outcomes |
| Recurring discovery/rechecks | `backend/app/workers/scheduling.py` | Opt-in bounded scheduling |
| Follow-up delivery | `backend/app/workers/outbox.py` | Replayable research-to-draft delivery after completion |
| Database schema change | New file in `supabase/migrations/` | Append a migration; do not edit applied history |
| Runtime database access | `backend/app/database/` or service methods | Parameterized SQL and explicit owner checks |
| Supabase API transport | `backend/app/database/supabase.py` | Fixed operation IDs and service-only RPCs |
| HTTP graph checkpoints | `backend/app/database/checkpoints.py` | Persisted state, pending writes and restart recovery |
| RPC implementation/catalog | `supabase/runtime_*.sql` + `backend/scripts/build_runtime_catalog.py` | Atomic commands and sealed operation allowlist; deploy changes as new migrations |
| Spend/latency tracing | `backend/app/observability/tracing.py` | Metadata, not private prompts or credentials |
| Usage versus reservations | `backend/app/observability/usage.py` | Provider-reported tokens/cost separately from capacity budgets; unknown costs remain unknown |
| Tests | `backend/tests/` | Test observable behavior and security boundaries |
| Local database testing | `tools/local-db/` + `docs/LOCAL_TESTING.md` | Disposable PostgreSQL/pgvector and offline full-suite execution |

Dependency flow: API → services → database; workers → agents → shared tools/research/retrieval.
Agents receive a context with their permitted dependencies. Infrastructure adapters do not import
HTTP routes. Frontend work can later live in a separate `frontend/` folder without moving backend code.

Keep exactly two primary agents. The research planner, verifier, classifier and scheduler are
workflow components, not additional autonomous agents. Add a focused module only when it owns
a real responsibility; avoid miscellaneous utility folders or one giant route file.
