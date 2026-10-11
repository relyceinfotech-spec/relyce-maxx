-- DESTRUCTIVE: removes legacy MAX data and installs MAXXX v2.
-- Run the complete file in the intended project's Supabase SQL Editor.
-- Stop legacy workers before executing. Auth, Storage and extensions are preserved.
-- Any error must ROLLBACK; do not execute selected statements after an error.
begin;
set local lock_timeout = '10s';
set local statement_timeout = '120s';
select pg_advisory_xact_lock(73184133);
-- Refuse reruns and existing v2 installs before deleting anything.
do $guard$ begin
 if to_regclass('public.research_jobs') is not null
 or to_regclass('agent_private.schema_migrations') is not null then
  raise exception 'Existing MAXXX v2 installation: replacement refused';
 end if;
end $guard$;

-- DESTRUCTIVE: deletes the obsolete MAX application tables and their data.
-- Execute only in the Supabase project configured in backend/.env.
-- Included by replace_legacy.sql inside one transaction; do not run separately.
-- RESTRICT deliberately aborts on unlisted dependencies. No managed schema is dropped.

drop table if exists
 research.max_conversation_messages,
 research.max_conversations,
 research.max_deleted_research_sessions,
 research.max_jobs,
 research.max_knowledge_documents,
 research.max_request_rate_limits,
 research.max_research_sessions,
 research.max_user_memories,
 research.max_user_quota_windows,
 content.max_autonomous_runs,
 content.max_exports,
 content.max_post_claims,
 content.max_post_followups,
 content.max_post_sources,
 content.max_posts,
 content.max_shares,
 content.max_topics,
 billing.customer_accounts,
 billing.plans,
 billing.subscriptions,
 billing.webhook_events
restrict;

-- Match only functions declared in the reviewed legacy migration files.
-- Identity arguments handle overloads without guessing signatures.
do $cleanup$
declare targets text;
begin
 select string_agg(format('%I.%I(%s)', n.nspname, p.proname,
   pg_get_function_identity_arguments(p.oid)), ', ' order by p.oid)
 into targets
 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
 where n.nspname || '.' || p.proname = any(array[
  'billing.max_apply_subscription_event',
  'content.max_publish_post', 'content.max_recover_interrupted_content',
  'content.max_save_post', 'content.max_write_worker_content',
  'public.max_publish_post', 'public.max_recover_interrupted_content',
  'public.max_recover_interrupted_sessions', 'public.max_save_knowledge_document',
  'public.max_save_post', 'public.max_search_knowledge_documents',
  'research.enforce_user_memory_active_limit',
  'research.enforce_user_memory_retained_limit',
  'research.max_append_conversation_user_message', 'research.max_assert_worker_lease',
  'research.max_cancel_job', 'research.max_claim_job', 'research.max_claim_job_by_id',
  'research.max_complete_conversation_assistant_message',
  'research.max_complete_conversation_assistant_message_with_metadata',
  'research.max_complete_job', 'research.max_consume_rate_limit',
  'research.max_consume_user_quota', 'research.max_delete_research_session',
  'research.max_enqueue_job', 'research.max_fail_job', 'research.max_heartbeat_job',
  'research.max_match_user_memories', 'research.max_recover_interrupted_sessions',
  'research.max_reject_deleted_session', 'research.max_save_knowledge_document',
  'research.max_search_knowledge_documents', 'research.max_write_worker_session'
 ]);
 if targets is not null then
  execute 'drop function ' || targets || ' restrict';
 end if;
end $cleanup$;

-- Remove only now-empty legacy schemas. Unexpected objects abort the transaction.
drop schema if exists research restrict;
drop schema if exists content restrict;
drop schema if exists billing restrict;


-- BEGIN 001_core.sql

-- Apply once to a NEW Supabase project. Backend writes use a private DB connection.
-- Client roles only receive selective reads and preference writes through RLS.
create extension if not exists vector with schema extensions;
create schema if not exists agent_private;
revoke all on schema agent_private from public, anon, authenticated;

create table public.topics (
 id uuid primary key default gen_random_uuid(), parent_id uuid references public.topics,
 slug text not null unique, name text not null, is_active boolean not null default true
);
create table public.profiles (
 user_id uuid primary key references auth.users on delete cascade,
 display_name text, created_at timestamptz not null default now()
);
create table public.conversations (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users on delete cascade,
 title text not null, created_at timestamptz not null default now(), unique(id,user_id)
);
create table public.messages (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users on delete cascade,
 conversation_id uuid not null, role text not null check(role in ('user','assistant')),
 content text not null, created_at timestamptz not null default now(),
 foreign key(conversation_id,user_id) references public.conversations(id,user_id) on delete cascade
);
create table public.research_jobs (
 id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users on delete cascade,
 conversation_id uuid, prompt text not null, mode text not null,
 post_id uuid, kind text not null default 'chat' check(kind in ('chat','draft')),
 payload jsonb not null default '{}', budget jsonb not null default '{}', idempotency_key uuid not null,
 status text not null default 'queued' check(status in ('queued','running','completed','failed','cancelled')),
 report jsonb, error_code text, lease_id uuid, leased_until timestamptz,
 attempts int not null default 0, checkpoint_generation int not null default 1,
 created_at timestamptz not null default now(),
 finished_at timestamptz, unique(user_id,idempotency_key), unique(id,user_id),
 foreign key(conversation_id,user_id) references public.conversations(id,user_id)
);
create index research_jobs_queue on public.research_jobs(status,created_at);
create table public.research_events (
 id bigint generated always as identity primary key, job_id uuid not null,
 user_id uuid not null references auth.users on delete cascade,
 stage text not null, data jsonb not null default '{}', created_at timestamptz not null default now(),
 foreign key(job_id,user_id) references public.research_jobs(id,user_id) on delete cascade
);
create index research_events_replay on public.research_events(job_id,id);
create table public.agent_runs (
 id uuid primary key default gen_random_uuid(), job_id uuid references public.research_jobs on delete cascade,
 user_id uuid not null references auth.users on delete cascade, agent text not null,
 status text not null default 'running', error_code text,
 started_at timestamptz not null default now(), finished_at timestamptz
);
create table public.model_usage (
 id bigint generated always as identity primary key, run_id uuid not null references public.agent_runs on delete cascade,
 model text not null, prompt_tokens int not null, completion_tokens int not null,
 reported_cost numeric, reserved_cost numeric not null, latency_ms int not null,
 status text not null, created_at timestamptz not null default now()
);
create table public.tool_executions (
 id bigint generated always as identity primary key, run_id uuid not null references public.agent_runs on delete cascade,
 tool text not null, status text not null, latency_ms int not null,
 error_code text, created_at timestamptz not null default now()
);
create table public.knowledge_chunks (
 id uuid primary key default gen_random_uuid(), user_id uuid references auth.users on delete cascade,
 job_id uuid references public.research_jobs on delete cascade,
 source_url text not null, title text not null, content text not null,
 embedding extensions.vector(1536), created_at timestamptz not null default now(),
 search_vector tsvector generated always as (to_tsvector('english',content)) stored,
 unique(job_id,source_url,content)
);
create index knowledge_fts on public.knowledge_chunks using gin(search_vector);
create index knowledge_owner on public.knowledge_chunks(user_id);

create table public.content_sources (
 id uuid primary key default gen_random_uuid(), name text not null, url text not null unique,
 topic_ids uuid[] not null, poll_minutes int not null check(poll_minutes>=30),
 enabled boolean not null default false, trust_tier text not null,
 etag text, last_modified text, last_polled_at timestamptz, last_success_at timestamptz,
 failure_count int not null default 0, next_poll_at timestamptz not null default now()
);
create table public.topic_queries (
 id uuid primary key default gen_random_uuid(), topic_id uuid not null references public.topics,
 query text not null, frequency_minutes int not null check(frequency_minutes>=60),
 enabled boolean not null default false, next_poll_at timestamptz not null default now(),
 unique(topic_id,query)
);
create table public.discovery_runs (
 id uuid primary key default gen_random_uuid(), source_id uuid references public.content_sources,
 query_id uuid references public.topic_queries, status text not null default 'running',
 item_count int not null default 0, searches int not null default 0, error_code text,
 started_at timestamptz not null default now(), finished_at timestamptz
);
create table public.story_clusters (
 id uuid primary key default gen_random_uuid(), fingerprint text not null unique,
 summary text not null, topic_ids uuid[] not null,
 status text not null default 'discovered', first_seen_at timestamptz not null default now()
);
create table public.discovered_items (
 id uuid primary key default gen_random_uuid(), source_id uuid references public.content_sources,
 canonical_url text not null unique, title text not null, published_at timestamptz,
 content_hash text not null, external_id text, cluster_id uuid not null references public.story_clusters,
 first_seen_at timestamptz not null default now()
);
create unique index discovered_publisher_id on public.discovered_items(source_id,external_id)
 where external_id is not null;
create table public.posts (
 id uuid primary key default gen_random_uuid(), created_by uuid not null references auth.users,
 research_job_id uuid not null references public.research_jobs,
 cluster_id uuid unique references public.story_clusters, current_version_id uuid,
 status text not null default 'draft' check(status in ('draft','published','rejected')),
 published_at timestamptz, created_at timestamptz not null default now(),
 unique(research_job_id)
);
alter table public.research_jobs add foreign key(post_id) references public.posts;
create table public.post_versions (
 id uuid primary key default gen_random_uuid(), post_id uuid not null references public.posts on delete cascade,
 research_job_id uuid not null references public.research_jobs,
 draft_job_id uuid not null unique references public.research_jobs,
 previous_version_id uuid references public.post_versions,
 title text not null, summary text not null, body text not null,
 claims jsonb not null, sources jsonb not null, topic_slugs text[] not null,
 correction_note text, status text not null default 'pending' check(status in ('pending','approved','rejected')),
 created_at timestamptz not null default now()
);
alter table public.posts add foreign key(current_version_id) references public.post_versions;
create table public.post_reviews (
 id uuid primary key default gen_random_uuid(), post_id uuid not null references public.posts,
 version_id uuid not null unique references public.post_versions,
 reviewer_id uuid not null references auth.users, decision text not null,
 note text not null, created_at timestamptz not null default now()
);
create table public.post_topics (
 post_id uuid not null references public.posts on delete cascade, topic_id uuid not null references public.topics,
 is_primary boolean not null default false, primary key(post_id,topic_id)
);
create unique index post_primary_topic on public.post_topics(post_id) where is_primary;
create table public.user_interests (
 user_id uuid not null references auth.users on delete cascade,
 topic_id uuid not null references public.topics, weight numeric not null check(weight between 0 and 1),
 blocked boolean not null default false, updated_at timestamptz not null default now(),
 primary key(user_id,topic_id)
);
create table public.user_interactions (
 user_id uuid not null references auth.users on delete cascade,
 post_id uuid not null references public.posts on delete cascade,
 kind text not null check(kind in ('save','like','hide','not_interested','read')),
 created_at timestamptz not null default now(), primary key(user_id,post_id,kind)
);

-- Deny by default on EVERY public table introduced by this migration.
do $$ declare t text; begin
 foreach t in array array['topics','profiles','conversations','messages','research_jobs',
 'research_events','agent_runs','model_usage','tool_executions','knowledge_chunks',
 'content_sources','topic_queries','discovery_runs','story_clusters','discovered_items',
 'posts','post_versions','post_reviews','post_topics','user_interests','user_interactions'] loop
 execute format('alter table public.%I enable row level security',t);
 execute format('revoke all on public.%I from anon, authenticated',t);
 end loop;
end $$;

grant select on public.topics,public.post_topics to anon,authenticated;
grant select(id,cluster_id,current_version_id,status,published_at,created_at)
 on public.posts to anon,authenticated;
-- Public clients can read editorial output and quoted claims, not retained article text.
grant select(id,post_id,previous_version_id,title,summary,body,claims,topic_slugs,
 correction_note,status,created_at) on public.post_versions to anon,authenticated;
create policy topics_read on public.topics for select using(is_active);
create policy posts_read on public.posts for select using(status='published');
create policy versions_read on public.post_versions for select using(status='approved' and exists
 (select 1 from public.posts p where p.id=post_id and p.status='published'));
create policy post_topics_read on public.post_topics for select using(exists
 (select 1 from public.posts p where p.id=post_id and p.status='published'));

do $$ declare t text; begin
 foreach t in array array['profiles','conversations','messages','research_jobs','research_events'] loop
 execute format('grant select on public.%I to authenticated',t);
 execute format('create policy owner_read on public.%I for select to authenticated using ((select auth.uid())=user_id)',t);
 end loop;
 foreach t in array array['user_interests','user_interactions'] loop
 execute format('grant select,insert,update,delete on public.%I to authenticated',t);
 execute format('create policy owner_access on public.%I for all to authenticated using ((select auth.uid())=user_id) with check ((select auth.uid())=user_id)',t);
 end loop;
end $$;
drop policy owner_access on public.user_interactions;
create policy owner_access on public.user_interactions for all to authenticated
 using((select auth.uid())=user_id)
 with check((select auth.uid())=user_id and exists
 (select 1 from public.posts p where p.id=post_id and p.status='published'));
-- Knowledge stays server-only; private chunks must never be public by accident.
create index messages_history on public.messages(conversation_id,created_at);
create index published_feed on public.posts(published_at desc) where status='published';
create index post_topics_lookup on public.post_topics(topic_id,post_id);


-- BEGIN 002_taxonomy.sql

insert into public.topics(slug,name) values
 ('ai','Artificial Intelligence'),('programming','Programming'),('cybersecurity','Cybersecurity');
insert into public.topics(slug,name,parent_id)
select x.slug,x.name,t.id from (values
 ('ai/models','Models & LLMs','ai'),('ai/agents','AI Agents','ai'),
 ('ai/research','AI Research','ai'),('ai/tools','AI Tools','ai'),
 ('ai/robotics','Robotics & Vision','ai'),
 ('programming/web','Web Development','programming'),
 ('programming/languages','Python & Languages','programming'),
 ('programming/cloud','Databases & Cloud','programming'),
 ('programming/open-source','Open Source','programming'),
 ('programming/devops','DevOps & Tooling','programming'),
 ('cybersecurity/vulnerabilities','Vulnerabilities','cybersecurity'),
 ('cybersecurity/appsec','Application Security','cybersecurity'),
 ('cybersecurity/ai-security','AI Security','cybersecurity'),
 ('cybersecurity/privacy','Privacy','cybersecurity')
) x(slug,name,parent) join public.topics t on t.slug=x.parent;


-- BEGIN 003_operations.sql

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


create table agent_private.schema_migrations (name text primary key);
revoke all on agent_private.schema_migrations from public, anon, authenticated;

insert into agent_private.schema_migrations values ('001_core.sql');

insert into agent_private.schema_migrations values ('002_taxonomy.sql');

insert into agent_private.schema_migrations values ('003_operations.sql');

notify pgrst, 'reload schema';
commit;
-- Next run maxxx-setup from backend/ to initialize private LangGraph checkpoints.
