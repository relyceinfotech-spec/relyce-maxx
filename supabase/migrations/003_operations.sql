create table agent_private.api_rate_windows (
 user_id uuid not null references auth.users on delete cascade,
 bucket bigint not null, requests integer not null,
 primary key(user_id,bucket)
);
create table agent_private.worker_heartbeats (
 worker_id uuid primary key, last_seen_at timestamptz not null default now()
);
create table agent_private.outbox (
 job_id uuid primary key references public.research_jobs on delete cascade,
 user_id uuid not null references auth.users on delete cascade,
 payload jsonb not null, status text not null default 'pending', error_code text,
 created_at timestamptz not null default now()
);
revoke all on all tables in schema agent_private from public,anon,authenticated;
-- Only one active job per conversation, including concurrent retries.
create unique index conversation_active_job on public.research_jobs(conversation_id)
 where conversation_id is not null and status in ('queued','running');
create unique index post_pending_revision on public.post_versions(post_id) where status='pending';
