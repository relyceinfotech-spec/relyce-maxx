# Verification status

## Current local verification — 2026-10-11

At the user's request, checks now use an isolated local PostgreSQL/pgvector database,
without hosted Supabase checks or deployments. **245 tests pass, 0 skipped**, including
the previously skipped four database tests. Ruff lint and formatting pass.

All migrations and protected RPC functions execute locally. The API/worker path covers
normal/follow-up/article Chat, web/deep research, draft generation, autonomous publication,
withholding gates, dedicated publishing identity, atomic corrections, follow-up context,
legacy review boundaries and feed visibility. Vector indexing/retrieval preserves ownership.
Provider HTTP 402 failure requires explicit retry; checkpoints recover across restarts.
Auth/model/search/page/embedding responses are doubles; SQL and checkpoints are real.

The latest [offline citation investigation](CHAT_CITATION_INVESTIGATION.md) adds server-generated
markers from validated mappings, footer-only rejection, evidence compaction and separate
reported-usage/reservation totals. No provider calls ran; live web acceptance remains pending.

Chat webpage navigation, exact passage offsets, structured citations, independent answer
coverage, adaptive collection caps and checkpointed external steps are now covered locally.
See [implementation and remaining acceptance gates](CHAT_WEB_READING.md).
The subsequent [offline coverage investigation](CHAT_COVERAGE_INVESTIGATION.md) adds sentence
mapping, distinct issue categories and limited-answer regressions without another live run.
Implementation used no paid calls. The subsequent single authorized v4 auto_web acceptance
run failed closed on answer coverage/contradictions, despite all five submitted claims being
supported. See [live results](LIVE_CHAT.md). No hosted changes or automatic retries ran.

This found and fixed pgvector operator resolution under the restricted RPC schema path.
The vector fix and new autonomous-publication/catalog migration remain local and **not deployed**.
Windows tests use a compatible selector event loop. Ruff lint and formatting pass.

Models are now Claude Haiku 5.5 for Chat/fast classification and GPT-6 Luna for research/Post.
All configured generative and decision IDs were verified in the appropriate live catalogs.
The latest standard Chat live run completed without 402 or truncation: 1,138/1,200 answer
tokens, `finish_reason=stop`, zero Serper calls, $0.0006702 total model cost.
The following automatic web run called Serper, extracted two pages and completed GPT-6
Luna generation (748 tokens, $0.0005363), but deterministic source/quote validation withheld
the report before entailment verification. Grounded-answer acceptance failed with zero final
citations. The full suite was not run. Hosted writes remained zero. See [LIVE_CHAT](LIVE_CHAT.md).
The subsequent single focused retest extracted four pages and passed source/quote/numbered
passage checks; independent verification then rejected claim index 0 and reported three
contradiction/coverage issues. It cost $0.0007959 model usage plus one Serper credit. No
extra live retry ran. Propagating extracted publication dates to the verifier and expanded
local diagnostic mappings were checked offline afterward. Live grounded-answer acceptance
remains failed. Post development and hosted writes stayed disabled.

Earlier, concise normal Chat passed a real Haiku run: 323/768 answer tokens, `finish_reason=stop`,
zero Serper calls, and $0.0002582 total model cost. The subsequent standard explanation
was credit-blocked (1,200 tokens requested, 841 affordable for that request). The earlier
GPT-6 Luna web attempt called Serper and extracted two pages but stopped on HTTP 402.
Live web/deep/follow-up acceptance remains pending. Hosted writes remained zero.
The latest reliability changes were checked only with doubles: structural completion,
verbosity, freshness routing, conflicting/snippet evidence, bounded owner context and
read-only credit gating. No additional live provider requests ran. See [LIVE_CHAT](LIVE_CHAT.md).
The offline [evidence audit](EVIDENCE_AUDIT.md) now maps recorded claims to captured passages
and citation targets. Eight focused cases cover missing evidence, mismatched/unknown citations,
duplicate IDs, invalid schemas, model-provided source substitution and semantic-review limits.
Historical artifact audits made zero external calls and do not establish live research acceptance.

The shared decision gateway is off by default and shadow-only when enabled. Its three-candidate
comparison corpus passes offline audits: 100 Chat examples, 100 real RSS headlines with
assistant-reviewed subject labels, and 50 difficult routing cases. This is corpus/adapter validation,
not live model accuracy. No live three-model comparison was attempted after HTTP 402.

See [LOCAL_TESTING](LOCAL_TESTING.md) for commands and report location. PGlite serializes
sessions; native PostgreSQL contention/CI and real provider quality remain separate
checks. The database is discarded after each run. Hosted results below are historical.

## Earlier hosted runtime verification — 2026-10-11

The backend now defaults to the Supabase Data API with the already supplied API credentials;
`DATABASE_URL` is not required in this mode. Protected RPCs and the fixed operation catalog
are deployed. Removed legacy schemas were also removed from the Data API exposed-schema
configuration, repairing a PGRST002 schema-cache failure.

- 55 offline tests pass; 4 optional direct-PostgreSQL tests remain skipped locally.
- Real backend `/health`, `/ready` and `/api/topics` return HTTP 200; 17 topics are available.
- Durable graph interruption/resumption with a fresh graph instance passes over real RPCs;
  checkpoint history and test-thread deletion were checked.
- Real Supabase Auth with two temporary accounts plus real queue/worker persistence passes
  chat submission idempotency, conflicting reuse (409), foreign-owner access (404), message
  history, evidence-backed draft persistence, hidden drafts and non-editor approval (403).
  Model/search responses in the successful flow were **deterministic test doubles**.
- Actual fetching/extraction of the official Python asyncio page passes. This exposed and
  fixed the TLS SNI bytes/string error that offline HTTP mocks had not caught.
- Approval, primary-topic publication and repeated-review idempotency were tested in a
  database transaction that was rolled back. The draft was confirmed never published.
- Temporary accounts, jobs, drafts/posts and checkpoints were removed; scoped catalog counts
  confirmed zero remaining records. No test content is published.

Real-model acceptance remains incomplete: a tiny normal-model probe succeeded, but the full
normal Chat request returned **OpenRouter HTTP 402**. Paid requests were stopped. Provider
HTTP status categories are now recorded without exposing provider bodies or secrets. The
successful deterministic flow does not establish real-model quality or paid-provider acceptance.
Container builds and the disposable direct-PostgreSQL CI suite remain unverified locally.

Older status notes below describe earlier implementation checkpoints.

## Live database verification — 2026-10-11

Supabase management access now works for the configured Signova project. Read-only catalog
checks found the replacement had already been applied: all 21 new public tables exist,
all have RLS, the three application migrations are recorded, 17 topics are seeded, and
the legacy `research`, `content` and `billing` schemas are absent. Auth and Storage remain.
The destructive replacement was therefore not rerun.

This session applied `initialize_private_graph_checkpoints` through the Supabase integration
using `supabase/checkpoints_setup.sql`, derived from the installed checkpoint package's
10 migrations. Ordinary indexes replace concurrent indexes for transactional creation
of these new empty tables. Verified all four checkpoint tables, migration versions 0–9,
RLS on each table, and no anon/authenticated usage of the private schema.

Backend runtime still needs `DATABASE_URL`. Real authenticated application acceptance and
database integration tests remain pending. The security advisor reports the existing
`public.rls_auto_enable()` execute-grant warnings and disabled leaked-password protection;
server-only tables with RLS and no client policies are intentionally default-deny.
Earlier preflight and no-mutation statements below describe earlier checkpoints.

Local implementation verification is separate from live product acceptance.

Checked locally:

Final result: **42 offline tests passed**, **4 real PostgreSQL tests skipped** because no test
database was configured. Ruff lint and formatting passed. A real localhost process smoke
check returned `/health` 200, no-key `/ready` 503, and an OpenAPI schema with 25 paths; the
script stopped its API child process afterward.

- Python 3.13.7 dependency installation and application imports.
- Ruff lint and formatting.
- Offline tests for chat routing, grounded synthesis, fabricated/rejected citations,
  inaccessible sources, knowledge ownership, editor authorization, no-key startup,
  SSRF protections, DNS pinning, response limits, provider schemas/usage, budgets,
  feed controls, RSS normalization, post drafting and approval boundaries.
- Actual localhost API startup and generated OpenAPI documentation without real credentials.

PostgreSQL integration tests and CI configuration are included for concurrent idempotency,
RLS isolation, lease cancellation and atomic/idempotent publication. They are skipped locally
without `TEST_DATABASE_URL`; this machine has no PostgreSQL/Docker service configured.
Their inclusion is not evidence that they have passed.

Pending after configuration:

1. Apply migrations/checkpointer setup to the intended Supabase project.
2. Verify real Supabase login and user isolation with two accounts.
3. Ask a live web/deep question and inspect source quality, dates, quotes, citations and spend.
4. Replay the timeline after restart, cancel a running job and exercise lease recovery.
5. Convert a verified report, review a draft, approve it and inspect feed/version history.
6. Enable a small real RSS/query registry; inspect deduplication, classifications and health.
7. Recheck a published story and confirm revisions need a new approval.
8. Build/test the container images and run the real database integration suite.

Credential preflight on 2026-10-11 used the authorized older checkout's environment without
printing secret values. The current ignored `backend/.env` contains the needed project,
Auth, OpenRouter, model and Serper settings. The original environment was not modified.

- Supabase Auth settings endpoint: HTTP 200 with the configured publishable key.
- OpenRouter key validation: HTTP 200.
- Configured chat, fast and research models appear in the current model catalog and
  advertise the structured-output parameter. Actual inference/schema compliance remains untested.
- Exactly one bounded Serper search: HTTP 200, three results.
- Supabase REST schema endpoint: HTTP 200. Its exposed table list was empty; this does
  not prove the database has no hidden or ungranted legacy tables.
- Follow-up inspection of the older client identified schema-specific REST access.
  Metadata requests for `research`, `content` and `billing` confirmed 21 exposed legacy
  tables in the configured project. See [legacy database findings](LEGACY_DATABASE.md).

`DATABASE_URL` is the only missing required configuration setting. Project API keys cannot
run the required schema DDL. No hosted tables were removed, no migrations were applied,
no model inference was run, and no content was published. Provider preflight is not
end-to-end research or publication acceptance.
