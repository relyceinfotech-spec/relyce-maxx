-- Server-owned operations for the Supabase Data API runtime.
-- Public wrappers are service-role-only; privileged implementation stays private.
create table if not exists agent_private.operation_catalog (
 id text primary key, statement text not null, returns_rows boolean not null
);
alter table agent_private.operation_catalog enable row level security;
revoke all on agent_private.operation_catalog from public,anon,authenticated,service_role;

create or replace function agent_private.execute_operation(operation_id text, parameters jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public,agent_private,pg_temp
as $fn$
declare spec record; parts text[]; statement text; i integer; row record; result jsonb='[]';
begin
 select * into spec from agent_private.operation_catalog where id=operation_id;
 if not found then raise sqlstate 'PT422' using message='Unknown database operation'; end if;
 parts=string_to_array(spec.statement,'%s');
 if jsonb_typeof(parameters)<>'array' or jsonb_array_length(parameters)<>cardinality(parts)-1 then
  raise sqlstate 'PT422' using message='Invalid operation parameters';
 end if;
 statement=parts[1];
 for i in 1..cardinality(parts)-1 loop
  if jsonb_typeof(parameters->(i-1)) not in ('string','number','boolean','null') then
   raise sqlstate 'PT422' using message='Invalid parameter type';
  end if;
  statement=statement || quote_nullable(parameters->>(i-1)) || parts[i+1];
 end loop;
 if spec.returns_rows then
  for row in execute statement loop result=result || jsonb_build_array(to_jsonb(row)); end loop;
 else execute statement;
 end if;
 return result;
end $fn$;

create or replace function agent_private.runtime_command(operation text, payload jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public,agent_private,pg_temp
as $fn$
declare j public.research_jobs; old public.research_jobs; p public.posts;
 v public.post_versions; cid uuid; pid uuid; rid uuid; vid uuid; t record;
 prior uuid; topics text[]; saved integer; decision text; result jsonb;
begin
 case operation
 when 'claim' then
  select * into j from public.research_jobs where (status='queued'
    or (status='running' and leased_until<now()))
    and (payload->>'job_id' is null or id=(payload->>'job_id')::uuid)
    order by created_at for update skip locked limit 1;
  if not found then return null; end if;
  update public.research_jobs set status='running',lease_id=gen_random_uuid(),
    leased_until=now()+least(3600,greatest(30,(payload->>'lease_seconds')::int))*interval '1 second',
    attempts=attempts+1 where id=j.id returning * into j;
  return to_jsonb(j);
 when 'submit_chat' then
  perform pg_advisory_xact_lock(hashtextextended((payload->>'user_id')||':'||(payload->>'idempotency_key'),0));
  select * into j from public.research_jobs where user_id=(payload->>'user_id')::uuid
    and idempotency_key=(payload->>'idempotency_key')::uuid;
  if found then
   if j.prompt<>payload->>'prompt' or j.mode<>payload->>'mode'
      or j.post_id is distinct from (payload->>'post_id')::uuid
      or (payload->>'conversation_id' is not null and j.conversation_id is distinct from (payload->>'conversation_id')::uuid) then
    raise sqlstate 'PT409' using message='Idempotency conflict';
   end if;
   return to_jsonb(j);
  end if;
  cid=(payload->>'conversation_id')::uuid;
  if cid is not null then
   perform 1 from public.conversations where id=cid and user_id=(payload->>'user_id')::uuid for update;
   if not found then raise sqlstate 'PT404' using message='Conversation not found'; end if;
  else
   insert into public.conversations(user_id,title) values((payload->>'user_id')::uuid,left(payload->>'prompt',120)) returning id into cid;
  end if;
  pid=(payload->>'post_id')::uuid;
  if pid is not null then
   perform 1 from public.posts where id=pid and status='published';
   if not found then raise sqlstate 'PT404' using message='Post not found'; end if;
  end if;
  if exists(select 1 from public.research_jobs where conversation_id=cid and status in ('queued','running')) then
   raise sqlstate 'PT409' using message='Conversation has an active job';
  end if;
  insert into public.messages(user_id,conversation_id,role,content)
    values((payload->>'user_id')::uuid,cid,'user',payload->>'prompt');
  insert into public.research_jobs(user_id,conversation_id,prompt,mode,post_id,idempotency_key)
    values((payload->>'user_id')::uuid,cid,payload->>'prompt',payload->>'mode',pid,(payload->>'idempotency_key')::uuid)
    returning * into j;
  return to_jsonb(j);
 when 'finish' then
  update public.research_jobs set status='completed',report=payload->'report',finished_at=now(),lease_id=null,leased_until=null
    where id=(payload->>'job_id')::uuid and lease_id=(payload->>'lease_id')::uuid
    and status='running' and leased_until>now() returning * into j;
  if not found then return 'false'::jsonb; end if;
  if j.conversation_id is not null and j.kind='chat' then
   insert into public.messages(conversation_id,user_id,role,content)
     values(j.conversation_id,j.user_id,'assistant',payload->'report'->>'answer');
  end if;
  update public.agent_runs set status='completed',finished_at=now() where id=(payload->>'run_id')::uuid;
  insert into public.research_events(job_id,user_id,stage,data)
    values(j.id,j.user_id,'completed',jsonb_build_object('verified',payload->'report'->'verified'));
  if payload->'follow_up' is not null and payload->'follow_up'<>'null'::jsonb then
   insert into agent_private.outbox(job_id,user_id,payload) values(j.id,j.user_id,payload->'follow_up') on conflict do nothing;
  end if;
  return 'true'::jsonb;
 when 'fail' then
  update public.agent_runs set status='failed',error_code=payload->>'code',finished_at=now() where id=(payload->>'run_id')::uuid;
  update public.research_jobs set status='failed',error_code=payload->>'code',finished_at=now(),lease_id=null,leased_until=null
    where id=(payload->>'job_id')::uuid and lease_id=(payload->>'lease_id')::uuid and status='running' returning * into j;
  if found then insert into public.research_events(job_id,user_id,stage,data)
    values(j.id,j.user_id,'failed',jsonb_build_object('error_code',payload->>'code')); end if;
  return 'true'::jsonb;
 when 'save_draft' then
  select * into j from public.research_jobs where id=(payload->>'job_id')::uuid
    and lease_id=(payload->>'lease_id')::uuid and status='running' and leased_until>now() for update;
  if not found then return null; end if;
  select * into v from public.post_versions where draft_job_id=j.id;
  if found then return jsonb_build_object('id',v.id,'post_id',v.post_id); end if;
  topics=array(select jsonb_array_elements_text(payload->'draft'->'topic_slugs'));
  if (select count(*) from public.topics where slug=any(topics) and is_active)<>(select count(distinct x) from unnest(topics) x) then
   raise sqlstate 'PT422' using message='Unknown topics'; end if;
  rid=(j.payload->>'research_job_id')::uuid;
  perform pg_advisory_xact_lock(hashtextextended(rid::text,0));
  select * into old from public.research_jobs where id=rid and user_id=j.user_id and status='completed';
  if not found or not coalesce((old.report->>'verified')::boolean,false) then
   raise sqlstate 'PT409' using message='Verified owned research required'; end if;
  if j.payload->>'post_id' is not null then
   select * into p from public.posts where id=(j.payload->>'post_id')::uuid for update;
   if not found or p.current_version_id is distinct from (j.payload->>'previous_version_id')::uuid then
    raise sqlstate 'PT409' using message='Stale revision'; end if;
  else
   insert into public.posts(created_by,research_job_id,cluster_id)
     values(j.user_id,rid,(j.payload->>'cluster_id')::uuid)
     on conflict(research_job_id) do update set research_job_id=excluded.research_job_id returning * into p;
  end if;
  insert into public.post_versions(post_id,research_job_id,draft_job_id,previous_version_id,
    title,summary,body,claims,sources,topic_slugs,correction_note)
    values(p.id,rid,j.id,p.current_version_id,payload->'draft'->>'title',payload->'draft'->>'summary',
      payload->'draft'->>'body',payload->'draft'->'claims',payload->'sources',topics,j.payload->>'correction_note')
    returning id into vid;
  return jsonb_build_object('id',vid,'post_id',p.id);
 when 'review' then
  select * into p from public.posts where id=(payload->>'post_id')::uuid for update;
  select * into v from public.post_versions where id=(payload->>'version_id')::uuid and post_id=p.id for update;
  if p.id is null or v.id is null then raise sqlstate 'PT404' using message='Post version not found'; end if;
  select r.decision into decision from public.post_reviews r where version_id=v.id;
  if found then
   if decision<>payload->>'decision' then raise sqlstate 'PT409' using message='Already reviewed'; end if;
   return jsonb_build_object('post_id',p.id,'version_id',v.id,'decision',decision);
  end if;
  if v.previous_version_id is distinct from p.current_version_id then raise sqlstate 'PT409' using message='Stale revision'; end if;
  if payload->>'decision'='approve' then
   select * into old from public.research_jobs where id=v.research_job_id and status='completed';
   if not found or not coalesce((old.report->>'verified')::boolean,false) then
    raise sqlstate 'PT409' using message='Verified research required'; end if;
   if (select count(*) from public.topics where slug=any(v.topic_slugs) and is_active)<>(select count(distinct x) from unnest(v.topic_slugs) x) then
    raise sqlstate 'PT409' using message='Invalid topics'; end if;
   delete from public.post_topics where post_id=p.id;
   insert into public.post_topics(post_id,topic_id,is_primary)
     select p.id,id,slug=v.topic_slugs[1] from public.topics where slug=any(v.topic_slugs) and is_active;
   update public.posts set status='published',current_version_id=v.id,published_at=coalesce(published_at,now()) where id=p.id;
  elsif payload->>'decision'<>'reject' then raise sqlstate 'PT422' using message='Invalid decision'; end if;
  update public.post_versions set status=case when payload->>'decision'='approve' then 'approved' else 'rejected' end where id=v.id;
  insert into public.post_reviews(post_id,version_id,reviewer_id,decision,note)
    values(p.id,v.id,(payload->>'user_id')::uuid,payload->>'decision',payload->>'note');
  return jsonb_build_object('post_id',p.id,'version_id',v.id,'decision',payload->>'decision');
 when 'set_interests' then
  perform pg_advisory_xact_lock(hashtextextended('interests:'||(payload->>'user_id'),0));
  saved=jsonb_array_length(payload->'interests');
  if saved>100 or (select count(distinct x->>'topic_id') from jsonb_array_elements(payload->'interests') x)<>saved then
   raise sqlstate 'PT422' using message='Invalid interests'; end if;
  if (select count(*) from public.topics where is_active and id in
    (select (x->>'topic_id')::uuid from jsonb_array_elements(payload->'interests') x))<>saved then
   raise sqlstate 'PT422' using message='Unknown topic'; end if;
  delete from public.user_interests where user_id=(payload->>'user_id')::uuid;
  insert into public.user_interests(user_id,topic_id,weight,blocked)
    select (payload->>'user_id')::uuid,(x->>'topic_id')::uuid,(x->>'weight')::numeric,(x->>'blocked')::boolean
    from jsonb_array_elements(payload->'interests') x;
  return jsonb_build_object('saved',saved);
 when 'ingest' then
  cid=(payload->>'cluster_id')::uuid;
  if cid is null then
   insert into public.story_clusters(fingerprint,summary,topic_ids)
     values(payload->>'fingerprint',payload->>'summary',array(select jsonb_array_elements_text(payload->'topic_ids'))::uuid[])
     on conflict(fingerprint) do update set fingerprint=excluded.fingerprint returning id into cid;
  end if;
  insert into public.discovered_items(source_id,canonical_url,title,published_at,content_hash,cluster_id,external_id)
    values((payload->>'source_id')::uuid,payload->>'url',payload->>'title',(payload->>'published_at')::timestamptz,
      payload->>'content_hash',cid,payload->>'external_id') on conflict do nothing returning id into vid;
  return jsonb_build_object('inserted',vid is not null);
 else raise sqlstate 'PT422' using message='Unknown command';
 end case;
end $fn$;

grant usage on schema agent_private to service_role;
revoke all on function agent_private.execute_operation(text,jsonb),agent_private.runtime_command(text,jsonb) from public,anon,authenticated;
grant execute on function agent_private.execute_operation(text,jsonb),agent_private.runtime_command(text,jsonb) to service_role;
create or replace function public.maxxx_query(operation_id text, parameters jsonb)
returns jsonb language sql security invoker set search_path='' as
 $$select agent_private.execute_operation(operation_id,parameters)$$;
create or replace function public.maxxx_command(operation text, payload jsonb)
returns jsonb language sql security invoker set search_path='' as
 $$select agent_private.runtime_command(operation,payload)$$;
revoke all on function public.maxxx_query(text,jsonb),public.maxxx_command(text,jsonb) from public,anon,authenticated;
grant execute on function public.maxxx_query(text,jsonb),public.maxxx_command(text,jsonb) to service_role;

create table if not exists agent_private.http_checkpoints (
 thread_id text not null, ns text not null, id text not null, parent text,
 checkpoint jsonb not null, metadata jsonb not null, metadata_filter jsonb not null,
 versions jsonb not null, primary key(thread_id,ns,id)
);
create table if not exists agent_private.http_blobs (
 thread_id text not null, ns text not null, channel text not null, version text not null,
 value jsonb not null, primary key(thread_id,ns,channel,version)
);
create table if not exists agent_private.http_writes (
 thread_id text not null, ns text not null, id text not null, task_id text not null,
 idx integer not null, channel text not null, value jsonb not null, task_path text not null,
 primary key(thread_id,ns,id,task_id,idx)
);
alter table agent_private.http_checkpoints enable row level security;
alter table agent_private.http_blobs enable row level security;
alter table agent_private.http_writes enable row level security;
revoke all on agent_private.http_checkpoints,agent_private.http_blobs,agent_private.http_writes from public,anon,authenticated,service_role;

create or replace function agent_private.checkpoint_operation(operation text, payload jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public,agent_private,pg_temp
as $fn$
declare x jsonb; result jsonb; tid text=payload->>'thread_id'; namespace text=coalesce(payload->>'ns','');
begin
 case operation
 when 'put' then
  for x in select jsonb_array_elements(payload->'blobs') loop
   insert into agent_private.http_blobs values(tid,namespace,x->>'channel',x->>'version',x->'value')
    on conflict(thread_id,ns,channel,version) do nothing;
  end loop;
  insert into agent_private.http_checkpoints values(tid,namespace,payload->>'id',payload->>'parent',
    payload->'checkpoint',payload->'metadata',payload->'metadata_filter',payload->'versions')
    on conflict(thread_id,ns,id) do update set checkpoint=excluded.checkpoint,metadata=excluded.metadata,
     metadata_filter=excluded.metadata_filter,versions=excluded.versions;
  return 'true'::jsonb;
 when 'writes' then
  for x in select jsonb_array_elements(payload->'writes') loop
   insert into agent_private.http_writes values(tid,namespace,payload->>'id',payload->>'task_id',
     (x->>'idx')::int,x->>'channel',x->'value',coalesce(payload->>'task_path',''))
    on conflict(thread_id,ns,id,task_id,idx) do update set channel=excluded.channel,value=excluded.value
     where excluded.idx<0;
  end loop;
  return 'true'::jsonb;
 when 'delete' then
  delete from agent_private.http_writes where thread_id=tid;
  delete from agent_private.http_blobs where thread_id=tid;
  delete from agent_private.http_checkpoints where thread_id=tid;
  return 'true'::jsonb;
 when 'get','list' then
  select coalesce(jsonb_agg(to_jsonb(c)||jsonb_build_object(
   'blobs',(select coalesce(jsonb_agg(jsonb_build_object('channel',b.channel,'value',b.value)),'[]')
     from agent_private.http_blobs b where b.thread_id=c.thread_id and b.ns=c.ns and b.version=c.versions->>b.channel),
   'writes',(select coalesce(jsonb_agg(jsonb_build_object('task_id',w.task_id,'channel',w.channel,'value',w.value)
      order by w.task_path,w.task_id,w.idx),'[]') from agent_private.http_writes w
      where w.thread_id=c.thread_id and w.ns=c.ns and w.id=c.id)
  ) order by c.id desc),'[]') into result from (
   select * from agent_private.http_checkpoints c
    where (tid is null or c.thread_id=tid)
     and (payload->>'ns' is null or c.ns=namespace)
     and (operation<>'get' or payload->>'id' is null or c.id=payload->>'id')
     and (payload->>'before' is null or c.id<payload->>'before')
     and (operation<>'list' or c.metadata_filter @> coalesce(payload->'filter','{}'))
    order by c.id desc limit case when operation='get' then 1 else least(1000,greatest(0,(payload->>'limit')::int)) end
  ) c;
  return result;
 else raise sqlstate 'PT422' using message='Unknown checkpoint operation';
 end case;
end $fn$;
revoke all on function agent_private.checkpoint_operation(text,jsonb) from public,anon,authenticated;
grant execute on function agent_private.checkpoint_operation(text,jsonb) to service_role;
create or replace function public.maxxx_checkpoint(operation text,payload jsonb)
returns jsonb language sql security invoker set search_path='' as
 $$select agent_private.checkpoint_operation(operation,payload)$$;
revoke all on function public.maxxx_checkpoint(text,jsonb) from public,anon,authenticated;
grant execute on function public.maxxx_checkpoint(text,jsonb) to service_role;

insert into agent_private.schema_migrations values ('20261011074804_runtime_rpc_return_types.sql') on conflict do nothing;
notify pgrst,'reload schema';
