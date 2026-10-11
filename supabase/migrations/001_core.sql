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
