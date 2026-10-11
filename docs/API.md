# API usage

The running `/docs` and `/openapi.json` are the schema source of truth. Authenticated endpoints
require a Supabase access token. Editor endpoints require an allowlisted verified Auth UUID.

| Endpoint | Access | Purpose |
| --- | --- | --- |
| `GET /health`, `GET /ready` | Public | Process and configuration/schema checks |
| `POST /api/chat` | User | Queue chat, knowledge, web or deep research; returns HTTP 202 |
| `GET /api/conversations` | Owner | List own conversation history |
| `GET /api/conversations/{id}/messages` | Owner | Read own messages |
| `GET /api/research/{id}` | Owner | Status, outcome, report and evidence |
| `GET /api/research/{id}/events` | Owner | Persisted SSE timeline and replay |
| `POST /api/research/{id}/cancel` | Owner | Cancel queued/running job |
| `POST /api/research/{id}/retry` | Owner | Explicitly restart a failed job |
| `POST /api/posts/from-research` | Research owner | Queue draft or proposed revision |
| `POST /api/posts/{id}/approve` | Editor | Approve or reject a specific version |
| `GET /api/posts/{id}` | Public | Current published version and source links |
| `GET /api/posts/{id}/versions` | User | Approved history; creator/editor can see pending versions |
| `GET /api/topics` | Public | Active taxonomy |
| `GET /api/feed` | User | `mode=for_you|following|latest`, `limit=1..100` |
| `GET/PUT /api/me/interests` | Owner | Replace explicit topic preferences |
| `POST/DELETE /api/me/interactions` | Owner | Like/save/read/hide/not-interested signals |
| `GET /api/admin/agent-runs`, `/{id}` | Editor | Run, model usage and tool traces |
| `GET /api/admin/drafts` | Editor | Pending drafts and their evidence |
| `GET/POST /api/admin/sources` | Editor | Curated RSS source registry and health |
| `GET/POST /api/admin/queries` | Editor | Scheduled topic news queries |
| `GET /api/admin/discovery` | Editor | Discovery runs and canonical clusters |
| `POST /api/admin/clusters/{id}/research` | Editor | Queue selected story investigation |
| `GET /api/admin/operations` | Editor | Queue, outbox and worker status |

## Autonomous research → publication → follow-up

The scheduler and Post worker publish validated system stories through the dedicated publisher;
no HTTP approval request is part of that workflow. `GET /api/posts/{id}` includes suggested
`followups`, version ID, claims, citations and correction note. Send a follow-up question to
`POST /api/chat` with `post_id`; Chat loads the current approved evidence and update history.
Its conversation and messages remain owned by the asking user. See
[AUTONOMOUS_PUBLISHING](AUTONOMOUS_PUBLISHING.md) for machine gates and configuration.

## Legacy user research → draft → review → feed

These optional legacy endpoints cannot approve autonomous drafts; both service and SQL return 409.

1. Send `POST /api/chat`:

```json
{
  "prompt": "Research recent changes in Python's release process using official sources",
  "mode": "deep",
  "idempotency_key": "a-new-client-generated-uuid"
}
```

Modes are `auto`, `normal`, `knowledge`, `web`, `deep`. Optional `conversation_id` continues
an owned conversation; optional `post_id` adds a published MAXXX post as initial context.
Only one active job per conversation is accepted. Reuse the same idempotency key when retrying
transport errors; changing its request body returns 409. UUID placeholders must be replaced.

2. Listen at `/api/research/{job_id}/events` with the same bearer token. Events:

- `research.snapshot`: current status on connection.
- `research.step`: persisted event with monotonic `id`, public `stage`, `data`, and timestamp.
- `research.done`: terminal status. Fetch `/api/research/{job_id}` for the report.

Send `Last-Event-ID` to replay events after the last received ID. Streams rotate after ten
minutes and can reconnect. Use authenticated `fetch` streaming or a client supporting bearer
headers; browser-native EventSource cannot set Authorization headers. No credentials in URLs.

3. When the report is `completed` and `report.verified=true`, send:

```json
{"research_job_id": "research-job-uuid"}
```

to `POST /api/posts/from-research`. It returns a new draft job. Its completed report contains
`post_id` and `version_id`; the editor can inspect the draft and evidence in `/api/admin/drafts`.

4. An editor sends to `/api/posts/{post_id}/approve`:

```json
{"version_id": "draft-version-uuid", "decision": "approve", "note": "Evidence reviewed"}
```

The post enters the feed atomically with topic mappings and a review audit. Repeating the
same review is idempotent. A conflicting review or stale revision returns 409.

5. For an update, complete fresh research and send `research_job_id`, `post_id`, and a nonempty
`correction_note` to `/api/posts/from-research`. The existing article remains current until the
new version is approved. Previous approved versions remain visible in version history.

## Feed controls

`PUT /api/me/interests` takes an array of `{topic_id, weight, blocked}` and replaces the
current preferences. Following a parent covers its descendants. Blocking a parent excludes
descendant posts. `POST /api/me/interactions` takes `{post_id, kind}`; remove a signal using
the same body with DELETE. Hide/not-interested exclude a post from all personalized views.

Ranking uses 55% explicit interest and 45% freshness with a 48-hour half-life. The response
includes score components and `ranking_version`. Popularity is not evidence. The current
API ranks the latest 300 published candidates and returns up to 100; it does not provide
an exhaustive/paginated archive or a trained recommendation model.

## Status/error behavior

Jobs: `queued → running → completed|failed|cancelled`. Completed does not imply verified:
normal chat has no external verification, and inaccessible/unsupported research can complete
with limitations. Unsupported conclusions are withheld and cannot generate posts.

Jobs with provider HTTP failures record sanitized categories such as `ProviderHTTP402`.
These indicate provider credit/access errors, not verified answers. Failed jobs require an
explicit retry after resolving the cause; the worker does not automatically repeat them.

HTTP 401 = invalid/missing token; 403 = editor access required; 404 = missing or unowned resource;
409 = state/idempotency conflict; 422 = invalid input/evidence; 429 = account request limit;
503 = configuration, database or authentication service unavailable. Job failures expose
sanitized exception categories; provider bodies and credentials are not returned.
