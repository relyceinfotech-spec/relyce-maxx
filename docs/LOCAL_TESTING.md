# Local backend testing

Development verification is currently local. Do not run hosted smoke scripts or
push migrations to Supabase until the user requests deployment.

From `D:\reserch maxxx`:

```powershell
npm ci --prefix tools/local-db
node tools/local-db/test.mjs
```

The Python environment must exist: run `uv sync --frozen --extra dev --python 3.13`
from `backend` first. Node 24.8.0 and Python 3.13.7 were verified here.

The runner starts a fresh in-memory [PGlite database](https://pglite.dev/docs/pglite-socket)
with pgvector on a random loopback port, supplies the guarded `maxxx_test` connection
to pytest, and discards the database afterward. It never deploys SQL or modifies `.env`.
Pinned JavaScript packages and their lockfile under `tools/local-db` are test tools,
not backend runtime dependencies.

`backend/tests/conftest.py` creates minimal Auth schemas/roles, applies every migration
in order and initializes private checkpoint tables. It refuses a nonempty application
database or a connection whose database name is not `maxxx_test`. Local runs block
outbound Python sockets. Auth, model, search, embeddings and pages are explicit doubles.

`backend/tests/test_local_workflows.py` runs the production Data API adapter against
an HTTP test transport that executes actual PostgreSQL RPC functions under `service_role`.
Encoding, the allowlist, grants, transactions and persistence are tested together.

Verified: **245 tests passed, 0 skipped**. Ruff lint and formatting pass.
The ignored machine-readable report is `backend/.local-state/local-tests.xml`.

Coverage includes normal/follow-up/article Chat, web/deep research, owner-scoped
knowledge/vector indexing, drafts, hidden drafts, editor approval, repeated review,
autonomous publication/withholding, independent source groups, topic/recency gates,
daily reservations, publishing permissions, correction history and shadow decision behavior,
feed visibility, idempotency/conflicts, RLS/role restrictions, atomic rollback,
cancelled leases, provider failure/explicit retry, and durable checkpoint restart,
history, filters, pending writes and deletion.

PGlite has one PostgreSQL session: local pools serialize transactions and use the
simple wire protocol. Concurrent application calls test idempotency, but do not prove
contention handling across native PostgreSQL sessions. CI retains a native pgvector
PostgreSQL service and the normal connection pool for that check. Hosted Auth,
PostgREST transport and real model quality remain separate live acceptance checks.
Local approvals disappear with the disposable database.

The suite found and fixed an unqualified pgvector distance operator under the restricted
RPC search path. `20261011080843_qualify_vector_distance_operator.sql` contains the
regenerated catalog and is **prepared locally, not deployed**. Deploy it together with
the backend change when the user authorizes a Supabase deployment.
