# Autonomous Post Agent

The normal workflow is scheduled discovery → verified research → generation → machine
checks → publication → scheduled recheck. No editor approval is required. A withheld
story remains private; the worker records `publication_withheld` and its failure category.
Chat remains user-driven and has no publishing tool.

## Code and permissions

- `app/workers/scheduling.py` uses `PUBLISHER_USER_ID`, independently of editor accounts.
- Discovery marks system investigations `autonomous=true`; the transactional outbox carries
  that provenance into the draft job. Chat request schemas cannot supply that flag.
- `app/agents/post/graph.py` checks claim coverage, entailment, contradictions, originality
  and safety. Worker publication requires its completed validation checkpoint and an exact
  match between the validated draft and persisted version.
- `app/services/autonomous.py` performs content checks and uses a dedicated Auth session,
  with the publishable key, to call `maxxx_autopublish`. It never sends the server key.
- The RPC wrapper uses invoker security. Its private implementation locks the publisher,
  job and post, verifies the dedicated `auth.uid()`, checks the lease, and publishes atomically.
  Anonymous users, ordinary authenticated users and service-role callers cannot publish through it.
- The legacy review endpoint rejects autonomous drafts in both Python and the SQL command
  dispatcher. It cannot bypass these gates.

## Machine gates

Every material draft claim must match a verified research claim with exact quotations from
at least two trusted publisher groups, including a primary source. Trust is an explicit
operator-managed host registry, not an LLM assessment. Two domains in one publisher group
count once. Publication requires active topics matching the discovered story's taxonomy,
complete research without recorded limitations, a recent dated discovery for an initial post,
and valid version lineage. Unknown dates and unresolved conflicts fail closed.

One canonical post per cluster is enforced by SQL. A normalized material-claim fingerprint
also blocks a second post for identical claims under a different headline. Semantic clustering
and entailment remain probabilistic; these checks do not guarantee factual truth or perfect
duplicate recognition. Safety includes model review and deterministic unsafe-content and
long-copy checks; it is not a comprehensive moderation benchmark.

Publisher rules enforce posting interval, daily version count, daily model reservations and
daily search count. Reservations are checked before research/draft external calls and include
failed jobs. Search credits are counted, not invented dollar prices. Discovery batches retain
their separate per-batch limits. Configured price ceilings must cover the selected providers.

Updates keep approved version history and require a visible correction/update note. The worker
first compares verified research against the current article and skips non-material changes.
`post_followups` contains suggested questions for the current approved version. Chat receives
post ID, version ID, tags, report, claims, source mappings and approved update history as reference
data, while fetched source passages remain the factual evidence. Conversation history stays
owner scoped.

## Configuration before a future deployment

Current changes are local; no new migration has been deployed. Keep
`AUTONOMOUS_PUBLISHING_ENABLED=false`, discovery/rechecks off and `DECISION_MODE=off`
until provisioning and hosted acceptance are explicitly requested.

When deploying later, apply `20261011082847_autonomous_publication.sql` and its refreshed
operation catalog. Provision a dedicated Supabase Auth account, add an enabled row to private
`publisher_accounts`, and configure trusted hosts/group ownership/tier in `publisher_sources`.
No sources are silently trusted by this migration. Register enabled RSS feeds and topic queries
through the existing admin interface. Source/topic provisioning is setup, not per-post approval.

Set `PUBLISHER_USER_ID` and a dedicated refresh token, or account email/password for autonomous
sign-in after restarts. Refresh-token rotation is held in process memory; email/password can
recover when an old configured token is no longer valid. A bare access token has limited lifetime.
These credentials belong only in the ignored local environment or a deployment secret store.
Then enable discovery, auto research, rechecks and autonomous publishing. Missing identity,
trust policy or credentials causes withholding; it never falls back to an editor or server key.

Local tests exercise real SQL gates, version history, permissions, outbox and worker publication
with Auth/model doubles. Native multi-session contention and live autonomous content quality
still require separate acceptance.
