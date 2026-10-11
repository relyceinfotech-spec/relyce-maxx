# Architecture decisions

The workspace is a new backend. Older Research MAX checkouts and their TypeScript contracts
are not imported. This implementation follows the pasted Python/FastAPI/LangGraph direction.

The default runtime accesses Supabase through service-only Data API RPCs, using the supplied
API credentials without requiring `DATABASE_URL`. Atomic mutations run inside database functions;
durable graph state uses private RPC checkpoint tables. Simple statements resolve by fixed
operation IDs against a server-owned allowlist, with values quoted by the database.
See [Supabase API runtime](SUPABASE_API.md). A direct PostgreSQL adapter remains optional.

One API process handles auth/contracts; independent worker processes consume a PostgreSQL
job table. `FOR UPDATE SKIP LOCKED` claims and expiring leases provide a durable initial queue
without adding Redis or requiring Supabase Queues to be enabled. A future PGMQ adapter can
replace queue operations without changing the research graphs or HTTP contracts.

The Chat Agent routes through normal, stored knowledge, web or deep workflows. Explicit modes
and freshness/search requests have deterministic handling; other requests use a fast model
classifier. Deep research plans focused searches, reads bounded evidence, composes a structured
report, checks quotes/entailment and can run one further corroboration round.

The Post Agent shares research evidence but has separate draft generation and validation.
Publication belongs to a controlled transactional service. Scheduled research, drafting,
publication and corrections require no editor approval. A dedicated Auth identity invokes
strict SQL gates; ordinary Chat cannot publish. See [autonomous publishing](AUTONOMOUS_PUBLISHING.md).
Optional [decision models](DECISIONS.md) are shared shadow components with no authority.

## Data and trust boundaries

- Supabase Auth validates every bearer token via `/auth/v1/user`, including expiry/revocation.
  Editor rights come from backend UUID configuration, never user-editable metadata.
- API services use backend-only Supabase RPCs and explicit owner predicates. RLS separately
  restricts direct client reads and preference writes; no client role can write jobs, drafts,
  publications, reviews, traces, knowledge or private checkpoints.
  Public column grants also keep retained full source text and research ownership metadata private.
- Sources are untrusted content. Models cannot execute arbitrary commands. HTTP fetching pins
  publicly resolved IP addresses, rechecks redirects, ignores environment proxies, restricts
  ports/schemes, enforces robots, serializes domain requests and bounds time/response size.
- Quotes must occur in source text; citations must refer to known source IDs. A second model
  call checks entailment, contradictions and missing claim coverage. These checks reduce errors
  but are probabilistic; `verified` means pipeline checks passed, not guaranteed truth.
- Public timelines contain operational summaries/source URLs, not private chain-of-thought.
  Checkpoints and counters are private; ordinary application logs omit prompts, credentials
  and provider response bodies. Authenticated reports/history remain owner-scoped.
- Each approved version retains its sources, claims, correction note, previous version link and
  publication audit. Publication atomically points to that version and updates topic mappings.

## Discovery and personalization

RSS is primary, Serper complementary. Registry schedules reserve due polls across workers;
ETag/Last-Modified support avoids redundant RSS downloads. Discoveries normalize URLs and dates,
deduplicate URLs and publisher IDs, then use a bounded structured classifier to propose event
clusters across publishers. Similarity/classifier output is a discovery heuristic, not evidence.
The controlled publisher independently checks topic alignment, trusted corroboration and
duplicate claim fingerprints before accepting a cluster's draft.

One canonical post per cluster is enforced by a unique constraint. A post can have many topics
and users rank the same published content according to explicit interests. Parent-topic matching,
blocking, hidden posts and diversity are deterministic. No separately generated news per user.

Knowledge retention saves verified public-source passages for their owning user. Keyword
retrieval is available first; optional explicit indexing enables pgvector neighbors. Other users'
private conversations and knowledge are never retrieved. There is no unrestricted crawler,
shell-execution tool, paywall bypass or automatic ingestion of private documents.

## Recovery and remaining scale work

Graph checkpoints use a dedicated private schema and a restricted serializer. Retry generations
separate fresh explicit retries from expired-lease resumptions. Budget reservations are saved
before external calls; lease fencing rejects stale completions. Provider billing is at least once
across crash windows, bounded by reservations. The transactional outbox makes follow-up draft
delivery replayable without duplicate canonical publications.

The initial worker executes one research job at a time; multiple workers can claim separate
jobs. Due discovery/rechecks are checked before claiming the next Chat job, so a busy queue
cannot starve scheduling. A currently executing bounded job can delay the next scheduler tick.
For larger traffic, run a dedicated scheduler/discovery worker and introduce global provider
quotas, retention jobs and monitoring. Autonomous research/draft jobs have daily model and search
reservations; discovery batches and ordinary Chat retain their separate run limits. These are
conservative execution limits, not a guarantee against external price changes or crash-window billing.
Browser-rendered extraction, rich document uploads, multilingual publishing,
trained feed ranking and freshness-aware cross-user research caching remain future extensions.
