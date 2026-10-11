-- Server-owned operations for the Supabase Data API runtime.
-- Public wrappers are service-role-only; privileged implementation stays private.
create table if not exists agent_private.operation_catalog (
 id text primary key, statement text not null, returns_rows boolean not null
);
alter table agent_private.operation_catalog enable row level security;
revoke all on agent_private.operation_catalog from public,anon,authenticated,service_role;

create or replace function agent_private.execute_operation(operation_id text, parameters jsonb)
returns jsonb language plpgsql security definer set search_path=pg_catalog,public,agent_private
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
returns jsonb language plpgsql security definer set search_path=pg_catalog,public,agent_private
as $fn$
declare j public.research_jobs; old public.research_jobs; p public.posts;
 v public.post_versions; cid uuid; pid uuid; rid uuid; vid uuid; t record;
 prior uuid; topics text[]; saved integer; decision text; result jsonb;
begin
 case operation
 when 'claim' then
  select * into j from public.research_jobs where status='queued'
    or (status='running' and leased_until<now())
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
  if not found then return false; end if;
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
  return true;
 when 'fail' then
  update public.agent_runs set status='failed',error_code=payload->>'code',finished_at=now() where id=(payload->>'run_id')::uuid;
  update public.research_jobs set status='failed',error_code=payload->>'code',finished_at=now(),lease_id=null,leased_until=null
    where id=(payload->>'job_id')::uuid and lease_id=(payload->>'lease_id')::uuid and status='running' returning * into j;
  if found then insert into public.research_events(job_id,user_id,stage,data)
    values(j.id,j.user_id,'failed',jsonb_build_object('error_code',payload->>'code')); end if;
  return true;
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
returns jsonb language plpgsql security definer set search_path=pg_catalog,public,agent_private
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
  return true;
 when 'writes' then
  for x in select jsonb_array_elements(payload->'writes') loop
   insert into agent_private.http_writes values(tid,namespace,payload->>'id',payload->>'task_id',
     (x->>'idx')::int,x->>'channel',x->'value',coalesce(payload->>'task_path',''))
    on conflict(thread_id,ns,id,task_id,idx) do update set channel=excluded.channel,value=excluded.value
     where excluded.idx<0;
  end loop;
  return true;
 when 'delete' then
  delete from agent_private.http_writes where thread_id=tid;
  delete from agent_private.http_blobs where thread_id=tid;
  delete from agent_private.http_checkpoints where thread_id=tid;
  return true;
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

-- Generated from literal application operations. Do not edit by hand.
delete from agent_private.operation_catalog;
insert into agent_private.operation_catalog values ('00d5ecfbbc58e1c692859c63359ecc86675f11fa59dfd1470c44562749db372e','update discovery_runs set status=''completed'',item_count=%s,searches=1,finished_at=now() where id=%s',false);
insert into agent_private.operation_catalog values ('074ce151899aff591e8512d508287b765fb0a17b96d013ac31cdece588c850f8','select id,parent_id,slug,name from topics where is_active order by slug',true);
insert into agent_private.operation_catalog values ('091e107c30aadd52293f9085c9bf733e7cb0e6a743ed57023c477253ab4ffcac','select id from topics where id=any(%s) and is_active',true);
insert into agent_private.operation_catalog values ('0a6bc09b6254f7af2388d8b5456207d222d84b4d38079eefa70c6ab7093c0bff','select v.title,v.summary,v.body,v.sources from posts p join post_versions v on v.id=p.current_version_id where p.id=%s and p.status=''published'' and v.status=''approved''',true);
insert into agent_private.operation_catalog values ('0dad0301ec290e016c2d0ca2a0f2372888f948c9b6a25b889c8af19437480fbb','insert into posts(created_by,research_job_id,cluster_id) values(%s,%s,%s) on conflict(research_job_id) do update set research_job_id=excluded.research_job_id returning *',true);
insert into agent_private.operation_catalog values ('0edc21150a57e80f8841eb091729b07b8e05e032e61e5cc13233dfe4a1921a00','select role,content,created_at,conversation_id from messages where user_id=%s and created_at<=%s and not(role=''user'' and content=%s) and (%s='''' or to_tsvector(''english'',content) @@ websearch_to_tsquery(''english'',%s)) order by created_at desc limit 20',true);
insert into agent_private.operation_catalog values ('0f05508db8535a9a4ec5714fa1acd8dbca885ae5779eb1584bc13976c0bdaf88','update research_jobs set budget=%s where id=%s and lease_id=%s and status=''running'' and leased_until>now() returning id',true);
insert into agent_private.operation_catalog values ('117413ebcab5bac8f627c2b788a952d9dce6f005fc91a324a45ce5a1248acdf0','insert into research_events(job_id,user_id,stage,data) select id,user_id,%s,%s from research_jobs where id=%s and status=''running'' and lease_id=%s and leased_until>now() returning id',true);
insert into agent_private.operation_catalog values ('130b3fa88d633fd460a78905f41683101ff15c39a1e25b68f0dc36bc7a7205d0','select decision from post_reviews where version_id=%s',true);
insert into agent_private.operation_catalog values ('14bebcb97bdad2ed996943c3beb509db6332c887f2ddfb95f4512d5d89d417e6','select * from discovery_runs order by started_at desc limit 50',true);
insert into agent_private.operation_catalog values ('157aefcfbf394979b0216a5662c25fdd634269f67262f3b32645d5afda6cc96b','select id from posts where id=%s and status=''published''',true);
insert into agent_private.operation_catalog values ('1881f742a59bba1b4d637549f8a37610542a458abc702d1486f8613c6a5c48d0','select * from content_sources order by name',true);
insert into agent_private.operation_catalog values ('1a04824ad80d375a325e351674e9be7264f42a813ae8092df14523e2da492a8a','update knowledge_chunks set embedding=%s::extensions.vector where id=%s and user_id=%s',false);
insert into agent_private.operation_catalog values ('1d3b87acc0e79b4346cd7b093fe53c4f8fa4fe2174ca32b00d552deddad5c1cd','select r.*,(select coalesce(sum(u.reported_cost),0) from model_usage u where u.run_id=r.id) reported_cost, (select sum(u.prompt_tokens+u.completion_tokens) from model_usage u where u.run_id=r.id) tokens from agent_runs r order by r.started_at desc limit %s',true);
insert into agent_private.operation_catalog values ('1e0a6519f288fb3462c4a311f9feeb49987bf1e135e62003b4ecdbb3a90a0faf','insert into discovered_items(source_id,canonical_url,title,published_at, content_hash,cluster_id,external_id) values(%s,%s,%s,%s,%s,%s,%s) on conflict do nothing returning id',true);
insert into agent_private.operation_catalog values ('1e6cbd7ff5816f9a211df30d5b05df4b8e830f0ae8d64b8bac3a8b6b6cf3852e','select canonical_url,title from discovered_items where cluster_id=%s limit 4',true);
insert into agent_private.operation_catalog values ('1f8b2114390b7fb7f9eb6e2264aba9bcba3d807a0e3d900ec16e828db2f2e29e','update agent_private.outbox set status=''failed'',error_code=%s where job_id=%s',false);
insert into agent_private.operation_catalog values ('2faafefac726675c06054a7316e9b5dbe3c75f5a8d09b7fea692113ee93f560c','update agent_private.outbox set status=''delivered'' where job_id=%s',false);
insert into agent_private.operation_catalog values ('32ecd259c63fb72f20dbe0a8afaac871e5bfd447798c6af07ae00734d8804ce3','select source_url,title,content from knowledge_chunks where user_id=%s and embedding is not null order by embedding <=> %s::extensions.vector limit 6',true);
insert into agent_private.operation_catalog values ('34aacb2dee11ea556809b1a910c9fd80c0419580222db4e3751eaffc8e4de86d','with recursive preferences as ( select t.id,u.weight,u.blocked from user_interests u join topics t on t.id=u.topic_id where u.user_id=%s union all select t.id,p.weight,p.blocked from topics t join preferences p on t.parent_id=p.id ), matches as ( select pt.post_id,max(case when not p.blocked then p.weight else 0 end) interest, bool_or(p.blocked) blocked from post_topics pt join preferences p on p.id=pt.topic_id group by pt.post_id ) select p.id,p.published_at,p.current_version_id,v.title,v.summary,v.body, coalesce(m.interest,0) interest,coalesce(m.blocked,false) blocked, exists(select 1 from user_interactions i where i.post_id=p.id and i.user_id=%s and i.kind in (''hide'',''not_interested'')) hidden, (select t.slug from post_topics pt join topics t on t.id=pt.topic_id where pt.post_id=p.id and pt.is_primary) primary_topic, (select array_agg(t.slug) from post_topics pt join topics t on t.id=pt.topic_id where pt.post_id=p.id) topics from posts p join post_versions v on v.id=p.current_version_id left join matches m on m.post_id=p.id where p.status=''published'' and v.status=''approved'' order by p.published_at desc,p.id limit 300',true);
insert into agent_private.operation_catalog values ('37747ce23ecc40339edd1ffcd5485583a216910bed3f6a2a8020da6d3ff389aa','select * from topic_queries order by query',true);
insert into agent_private.operation_catalog values ('3b64a061a1970f9b18af90f79e4f05f7e897e03c13ff5bdb6f97722635d8046b','select id,title,created_at from conversations where user_id=%s order by created_at desc limit 100',true);
insert into agent_private.operation_catalog values ('3b84e941bd88dfb0c73bae5dc20ebe4034315752cef2141cc8fa86e1b50755bc','select id,slug,name from topics where is_active',true);
insert into agent_private.operation_catalog values ('3d2add91cbdb1d0a45d18335e0b994e50ab680baea9b900788229ab8ebfab091','insert into research_jobs(user_id,prompt,mode,post_id,payload,idempotency_key) values(%s,%s,''deep'',%s,%s,%s) on conflict(user_id,idempotency_key) do nothing',false);
insert into agent_private.operation_catalog values ('3da276a05922d1cc5ec692d9b93d7347ada0881afc606566b0d4098468beb717','select source_url,title,content from knowledge_chunks where user_id=%s and search_vector @@ websearch_to_tsquery(''english'',%s) order by ts_rank(search_vector,websearch_to_tsquery(''english'',%s)) desc limit 6',true);
insert into agent_private.operation_catalog values ('3f7676c5631acacde9e01f2a0bbd9f24be57c5f22c6e06327bbfbb0be818423a','delete from user_interests where user_id=%s',false);
insert into agent_private.operation_catalog values ('4079ef488312d18e9919442dec1f826fc7e31bb22bee449b4725e5542289813c','select id,created_by,status from posts where id=%s',true);
insert into agent_private.operation_catalog values ('40f16ec2afa8c9a16b36c818d20ce1637ca3b52316c528f20e5e1dfa2f550085','update agent_runs set status=''failed'',error_code=%s,finished_at=now() where id=%s',false);
insert into agent_private.operation_catalog values ('4593fdbe95fb1e9fb57c1252ed31ad3ad63be98cd2af126243ee5c05633abbf6','select topic_id,weight,blocked from user_interests where user_id=%s',true);
insert into agent_private.operation_catalog values ('45dde524221e9e88321da78ded55df9da54b00be30a7221a8599708d28aee387','select status,count(*) from research_jobs group by status',true);
insert into agent_private.operation_catalog values ('480b85f677780e0d92895e2c2f7d9c8d500e71ff86724430caa21f56bfbf5370','select id,stage,data,created_at from research_events where job_id=%s and user_id=%s and id>%s order by id limit 100',true);
insert into agent_private.operation_catalog values ('4d8fbb7430afbf37ef3d73ebd847427f9fec0ae2ae155976b49fa1534745f767','select * from agent_private.outbox where status=''pending'' order by created_at for update skip locked limit 1',true);
insert into agent_private.operation_catalog values ('5676330f8ea82f521d264000cd5b90227c0bb6c7736ded3fd822bdc3c867b9e6','insert into research_events(job_id,user_id,stage,data) values(%s,%s,''completed'',%s)',false);
insert into agent_private.operation_catalog values ('57f4228d565515b312b35afdfd43bc18ec375f04b38c60a04b1b6d81e73d9092','select id,summary from story_clusters where first_seen_at>now()-interval ''7 days'' order by first_seen_at desc limit 30',true);
insert into agent_private.operation_catalog values ('59176262a135027bf69655eecd6a5369da6acea75b3455bcd8a7be1e3f6e61ba','insert into user_interests(user_id,topic_id,weight,blocked) values(%s,%s,%s,%s)',false);
insert into agent_private.operation_catalog values ('5b167f9a954183ec81e620c0fc13d4238679d2b0bc451966e893e6e8468e0bac','insert into user_interactions(user_id,post_id,kind) values(%s,%s,%s) on conflict(user_id,post_id,kind) do update set created_at=now()',false);
insert into agent_private.operation_catalog values ('5b4996de66a864b5bfd7139d030078d867d5a49e045ed7157b2ac38d9b8f872e','insert into research_jobs(user_id,prompt,mode,payload,idempotency_key) values(%s,%s,''deep'',%s,%s) on conflict(user_id,idempotency_key) do update set idempotency_key=excluded.idempotency_key returning id,status',true);
insert into agent_private.operation_catalog values ('5dbc8de2137bc9336e3984021cea15f8a9b9ce1612165fd70fbe3f5032a0b7bd','insert into agent_private.worker_heartbeats(worker_id) values(%s) on conflict(worker_id) do update set last_seen_at=now()',false);
insert into agent_private.operation_catalog values ('5e291cc21cc71938ce6fb399d692e490184153d949c615688cbea838f8b97cb2','insert into tool_executions(run_id,tool,status,latency_ms,error_code) values(%s,%s,%s,%s,%s)',false);
insert into agent_private.operation_catalog values ('5f55495f989bf0e136ade9f61d20afbc685c0e5a7beaf115c779ba5f85b8ae08','with failed as ( update research_jobs set status=''failed'',error_code=%s,finished_at=now(), lease_id=null,leased_until=null where id=%s and lease_id=%s and status=''running'' returning id,user_id ) insert into research_events(job_id,user_id,stage,data) select id,user_id,''failed'',%s from failed returning id',true);
insert into agent_private.operation_catalog values ('60dd9a74944cfc74e34bc30bd832b2740d34e3c3c3dbb5bc5f469d9c31a67937','update research_jobs set status=''completed'',report=%s,finished_at=now(), lease_id=null,leased_until=null where id=%s and lease_id=%s and status=''running'' and leased_until>now() returning id',true);
insert into agent_private.operation_catalog values ('6186b77062c3b1b965b58c5bd169b778c03306963425a07eddbe77e7ee41e479','insert into post_versions(post_id,research_job_id,draft_job_id,previous_version_id, title,summary,body,claims,sources,topic_slugs,correction_note) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id,post_id',true);
insert into agent_private.operation_catalog values ('61be2081593e8c5a5ddac91171fe8fa71fd3519d4b5e80eef0bd4fddb38e1b8d','select * from posts where id=%s',true);
insert into agent_private.operation_catalog values ('6376840793d3007277f9f66aa15b568557007f1edbd10ec7f3796b5356fc9bf4','select * from model_usage where run_id=%s order by id',true);
insert into agent_private.operation_catalog values ('64cfe9c4552e23c4577585d35240f2269e600f389e031c0dcb2f8d9535c7a4fa','select id from research_jobs where id=%s and status=''running'' and lease_id=%s and leased_until>now() for update',true);
insert into agent_private.operation_catalog values ('6520a57053bcc6704a998f54453ced71d770c1a35bb39bf25a900d37dfc7112f','update research_jobs set status=''queued'',error_code=null,finished_at=null, checkpoint_generation=checkpoint_generation+1,budget=''{}''::jsonb where id=%s and user_id=%s and status=''failed'' returning *',true);
insert into agent_private.operation_catalog values ('6535dcc0ff5ffbde5d4be73dde4c5d406b76bee7a4eb19ef49c8ada05eb788e9','insert into messages(conversation_id,user_id,role,content) values(%s,%s,''assistant'',%s)',false);
insert into agent_private.operation_catalog values ('66a9b94d713db81515576156b3150e623e819d07a9f2062ff951f1b1d94a159c','select report from research_jobs where id=%s and status=''completed''',true);
insert into agent_private.operation_catalog values ('6a579fc42ac545d25781598611313b4b8ccae181f8f560047d98df66c4066737','insert into agent_runs(user_id,agent) values(%s,''discovery'') returning id',true);
insert into agent_private.operation_catalog values ('6b031afc098697ac272a8517d5fd31c836d470839c959a2dcb1b73de2a0634e6','select v.title,v.summary,v.body from posts p join post_versions v on v.id=p.current_version_id where p.id=%s and p.status=''published''',true);
insert into agent_private.operation_catalog values ('74384a607464cf38fe09f1e4f393e98e1d0cee08bf91fd25a5d7e5df2c715b6f','update discovery_runs set status=''completed'',item_count=%s,finished_at=now() where id=%s',false);
insert into agent_private.operation_catalog values ('754d17715705415af52d964325d878c6069660ae9af319ef83840dd05899efab','select id from discovered_items where canonical_url=%s or (source_id=%s and external_id=%s)',true);
insert into agent_private.operation_catalog values ('75ccbdd479269c2739047375c8eb41a048a7751dd8f9f2eac0ee28380f9474f2','select id from conversations where id=%s and user_id=%s for update',true);
insert into agent_private.operation_catalog values ('762407c1010fd55fbb778781a685015e63535198d945467edf0818c452980ece','select role,content from messages where conversation_id=%s and user_id=%s and created_at<%s order by created_at desc,id desc limit 12',true);
insert into agent_private.operation_catalog values ('76b46a5a287f6c37deafbf9ea54ab541f2eb3e7a3700f23eb1b5daac8fe7e2ed','insert into discovery_runs(source_id) values(%s) returning id',true);
insert into agent_private.operation_catalog values ('7ac94f7bda033e7d5a57cca1346fa2924d4830ee5b93247747b2a432c1ebe894','insert into research_jobs(user_id,conversation_id,prompt,mode,post_id,idempotency_key) values(%s,%s,%s,%s,%s,%s) returning *',true);
insert into agent_private.operation_catalog values ('7b35fe76022fe6bb4f16cd87f15a27a7e5c228524391760865092c1f0c644ef9','insert into messages(user_id,conversation_id,role,content) values(%s,%s,''user'',%s)',false);
insert into agent_private.operation_catalog values ('810deb49cc3649a6c4abf2ef5c54815ac38959f6ea6b87f3f7241f50d924e76c','select * from post_versions where id=%s and post_id=%s for update',true);
insert into agent_private.operation_catalog values ('813b582503a8b3aef34c2eed368e7001a13a17bdbb2210b60890343858c93242','select id,post_id from post_versions where draft_job_id=%s',true);
insert into agent_private.operation_catalog values ('8141ec8b44e94c190508ff5d868007e51e201fc70fa4e6bc0dfaa951dc81fb73','select pg_advisory_xact_lock(hashtextextended(%s,0))',true);
insert into agent_private.operation_catalog values ('81b1e58b53ac16e8cb64e1134d0e180bbd4d955fe4c3e58082e0c110ea0fae08','insert into post_topics values(%s,%s,%s)',false);
insert into agent_private.operation_catalog values ('83e10c113b518d8b458db383fc0972ea8b1a3fb027033ceeffbb0b3ca33e3c21','insert into story_clusters(fingerprint,summary,topic_ids) values(%s,%s,%s) on conflict(fingerprint) do update set fingerprint=excluded.fingerprint returning id',true);
insert into agent_private.operation_catalog values ('88c8d423519e45911ec3ce24c592b123f2d5df6f36f5fcdf0da58ad72e010b08','insert into agent_private.outbox(job_id,user_id,payload) values(%s,%s,%s) on conflict(job_id) do nothing',false);
insert into agent_private.operation_catalog values ('8d3a30a3ca3f7a7df7c75251f9c377c6454209129473dad18762a73fe7bbd50c','delete from user_interactions where user_id=%s and post_id=%s and kind=%s',false);
insert into agent_private.operation_catalog values ('90508ad3b6cb3992af7ce60ef9a47c48be91230a689e958e09a463b24584d71c','select id,post_id,title,summary,body,claims,sources,topic_slugs,previous_version_id,correction_note,created_at from post_versions where status=''pending'' order by created_at limit 100',true);
insert into agent_private.operation_catalog values ('94803c951e30f524ea1878a2f4793734fe4dee4bf676bf1352a6b13b7c0b2212','select * from research_jobs where user_id=%s and idempotency_key=%s',true);
insert into agent_private.operation_catalog values ('9628e509d9e7d149dc3e25477d22acb6cb8279b6100d064be8fe4bfcd70c5221','select * from post_versions where id=%s and post_id=%s',true);
insert into agent_private.operation_catalog values ('9a9b3b7700374311ab0b39cbfd75f564396f12648e2a18650783de5dfd68f0ee','select max(last_seen_at) last_seen_at from agent_private.worker_heartbeats',true);
insert into agent_private.operation_catalog values ('9c143a8b25870cb61816cbe55bc3149ea5215021526473335979c2779f30b9eb','update posts set status=''published'',current_version_id=%s, published_at=coalesce(published_at,now()) where id=%s',false);
insert into agent_private.operation_catalog values ('9c308f2eacb85d6c74d780d5df9852331d1e254cf9bb3dc43419b02d3f756e20','select * from research_jobs where id=%s and user_id=%s',true);
insert into agent_private.operation_catalog values ('a1f524e97498fc35e75d5ac825de3e8c893119d191453d434390239ea511fb94','select * from tool_executions where run_id=%s order by id',true);
insert into agent_private.operation_catalog values ('a26f424103fd07cbfa54473d0da94250fd05c16ee828b3449dbef96a2e72cf31','delete from post_topics where post_id=%s',false);
insert into agent_private.operation_catalog values ('a30b07de43b5ec49d38b68a1e17802720ca86de82de96ffe562f85c815437302','with candidate as ( select id from research_jobs where status=''queued'' or (status=''running'' and leased_until<now()) order by created_at for update skip locked limit 1 ) update research_jobs j set status=''running'',lease_id=gen_random_uuid(), leased_until=now()+(%s * interval ''1 second''),attempts=attempts+1 from candidate c where j.id=c.id returning j.*',true);
insert into agent_private.operation_catalog values ('a50806c65b79211926b03e2e6d6ab92e8aac7b8e6d2587357d9db5c772f5420a','update discovery_runs set status=''failed'',error_code=%s,finished_at=now() where id=%s',false);
insert into agent_private.operation_catalog values ('a65a0e0515ac9c0874ce654105060cb6ca2cf1858fd3cbcb09c8b0e00bdb02f4','select id,content from knowledge_chunks where user_id=%s and embedding is null order by created_at limit %s',true);
insert into agent_private.operation_catalog values ('a65a59ec6314b148b4f71697735963c42af802756dc51b7aa6df96fb6da9f7d0','insert into post_reviews(post_id,version_id,reviewer_id,decision,note) values(%s,%s,%s,%s,%s)',false);
insert into agent_private.operation_catalog values ('a712f1ee216f7883f59a23f57afcbfc8688fedfb09cfa558110556601026f967','with due as (select id from topic_queries where enabled and next_poll_at<=now() order by next_poll_at for update skip locked limit 2) update topic_queries q set next_poll_at=now()+(frequency_minutes * interval ''1 minute'') from due d where q.id=d.id returning q.*',true);
insert into agent_private.operation_catalog values ('a7ae5dab3cd9c01a051be387022c1981d0e1e38709538f44374ec4681e291bb0','insert into knowledge_chunks(user_id,job_id,source_url,title,content) values(%s,%s,%s,%s,%s) on conflict do nothing',false);
insert into agent_private.operation_catalog values ('a883e44d3c94ce95ca3970dbe835f009a9d0f4d243e429387227304c964e4ce2','select id from conversations where id=%s and user_id=%s',true);
insert into agent_private.operation_catalog values ('a9d4320964de827b3cf872e5b7821e29f64136af1e51ab8baa95faf865992c92','insert into conversations(user_id,title) values(%s,%s) returning id',true);
insert into agent_private.operation_catalog values ('aaa6f20fd283270b17a1cd7b2236c3a75637f2935660d4b2c285b56379717be2','select sources from post_versions where id=%s',true);
insert into agent_private.operation_catalog values ('ad9503f7023dc70abca36693622a8278ac9b5e5ec4afb7465ab08314f7e7616c','select slug,name from topics where is_active',true);
insert into agent_private.operation_catalog values ('afe581deeeedeb0d3d32b43963b3124d5390eccc99d036dd45c5149dabc182d8','update agent_runs set status=''completed'',finished_at=now() where id=%s',false);
insert into agent_private.operation_catalog values ('b6ad6f0cc4c74124af9905e662c492af6ce5afb9cf4a09e9da923dae1797f28d','with cancelled as ( update research_jobs set status=''cancelled'',lease_id=null,leased_until=null, finished_at=now() where id=%s and user_id=%s and status in (''queued'',''running'') returning id,user_id ) insert into research_events(job_id,user_id,stage,data) select id,user_id,''cancelled'',''{}'' from cancelled returning id',true);
insert into agent_private.operation_catalog values ('b8c01c37b9b7049997a181248457d800fa10a6d3594447da2e08acf580e9158a','update agent_runs set status=''cancelled'',finished_at=now() where id=%s',false);
insert into agent_private.operation_catalog values ('bb8101e2704c59cc0bd27f1078f48d919076307f67190d84d751707dd85448d4','update content_sources set last_success_at=now(),failure_count=0, etag=coalesce(%s,etag),last_modified=coalesce(%s,last_modified) where id=%s',false);
insert into agent_private.operation_catalog values ('bcbf51c27e086d7f35b8a7b9aae0bf7b93d3f142309c76835eebf6641f13a5c9','select 1 from research_jobs where conversation_id=%s and status in (''queued'',''running'')',true);
insert into agent_private.operation_catalog values ('bdd5cd4c80d4d8396a0884d9e69330045bbb18c259d3899524537a7df19e5116','select c.id from story_clusters c where c.first_seen_at>now()-interval ''3 days'' and not exists(select 1 from posts p where p.cluster_id=c.id) and not exists(select 1 from research_jobs j where j.payload->>''cluster_id''=c.id::text) order by c.first_seen_at desc limit 1',true);
insert into agent_private.operation_catalog values ('c1b1514a55e40effcd344d4a51b3250b4fd4dd9dd268d56e9a1f5990924b2b9f','insert into model_usage(run_id,model,prompt_tokens,completion_tokens, reported_cost,reserved_cost,latency_ms,status) values(%s,%s,%s,%s,%s,%s,%s,%s)',false);
insert into agent_private.operation_catalog values ('c1bdb90d100e841bddf478f3053b32f93d433de96eb62a83251d3fa6c53d408a','select * from agent_runs where id=%s',true);
insert into agent_private.operation_catalog values ('c815af2b620e9432d417332ca8ad1ab1892b140a2f3bf38704267ab324f17393','select p.id,v.title from posts p join post_versions v on v.id=p.current_version_id where p.status=''published'' and not exists(select 1 from post_versions pv where pv.post_id=p.id and pv.status=''pending'') and not exists(select 1 from research_jobs j where j.post_id=p.id and ( j.status in (''queued'',''running'') or j.created_at>now()-(%s * interval ''1 hour''))) order by p.published_at desc limit 1',true);
insert into agent_private.operation_catalog values ('c9ad40be399a6336f8ad9b5f9987d9d6f0cf4bf2c5e9548174420c14c9362a7a','insert into agent_runs(job_id,user_id,agent) values(%s,%s,%s) returning id',true);
insert into agent_private.operation_catalog values ('cba88897fa4e35f6e5c47697fcf1323a73d5cda4f8bb38c60b6516ebd7760eef','with due as (select id from content_sources where enabled and next_poll_at<=now() order by next_poll_at for update skip locked limit 3) update content_sources s set last_polled_at=now(), next_poll_at=now()+(poll_minutes * interval ''1 minute'') from due d where s.id=d.id returning s.*',true);
insert into agent_private.operation_catalog values ('cc4609af586a6b2678aa8ad21a6358d9737499a55653a928890c4ee37de0fd54','delete from agent_private.api_rate_windows where bucket<%s',false);
insert into agent_private.operation_catalog values ('cc4b10a7470d6ba1ebffe7765e15f07bc56b8f9e49881649a5821bc07caaf752','insert into content_sources(name,url,topic_ids,poll_minutes,enabled,trust_tier) values(%s,%s,%s,%s,%s,%s) on conflict(url) do update set name=excluded.name, topic_ids=excluded.topic_ids,poll_minutes=excluded.poll_minutes, enabled=excluded.enabled,trust_tier=excluded.trust_tier returning *',true);
insert into agent_private.operation_catalog values ('ccd38effbedcfd30fd266c2891b0c986344e629fc070d41fd0cfe735054a1ccb','insert into topic_queries(topic_id,query,frequency_minutes,enabled) values(%s,%s,%s,%s) on conflict(topic_id,query) do update set frequency_minutes=excluded.frequency_minutes, enabled=excluded.enabled returning *',true);
insert into agent_private.operation_catalog values ('cfd882b31e9b346f691910b629e708463d2273036e217da3ddd0f794a4682d2b','select * from story_clusters where id=%s',true);
insert into agent_private.operation_catalog values ('d210d68043c5bd3b957eaa2ea6ead94751014fda6445207db6822ffe3624c5f3','select to_regclass(''public.research_jobs'') is not null and to_regclass(''agent_private.schema_migrations'') is not null and to_regclass(''agent_private.checkpoint_migrations'') is not null and to_regclass(''agent_private.checkpoints'') is not null and to_regclass(''agent_private.checkpoint_blobs'') is not null and to_regclass(''agent_private.checkpoint_writes'') is not null and to_regclass(''agent_private.http_checkpoints'') is not null and to_regclass(''agent_private.operation_catalog'') is not null as ready',true);
insert into agent_private.operation_catalog values ('d439d8e703f48b2f8883535a82eb874213fe85e6f95fe620bd610bf9e79b27e9','insert into research_jobs(user_id,prompt,mode,kind,payload,idempotency_key) values(%s,''Generate an evidence-backed post draft'',''normal'',''draft'',%s,%s) on conflict(user_id,idempotency_key) do update set idempotency_key=excluded.idempotency_key returning *',true);
insert into agent_private.operation_catalog values ('d760f8b0c3f699fc31f65f941c9fbb9aa5f4a314715172bf6d6db1e1c718cd5f','insert into agent_runs(user_id,agent) values(%s,''index'') returning id',true);
insert into agent_private.operation_catalog values ('d913718763972bb76ec6d5b9685aea7532925efcaab85ab60f9acd859df80129','select * from posts where id=%s for update',true);
insert into agent_private.operation_catalog values ('dad29a40f61301d67867ebecb09d70177539c5ec83ab528f64873e0d6ee6d1c3','select status,count(*) from agent_private.outbox group by status',true);
insert into agent_private.operation_catalog values ('db1a961ab72fc6b960086fe6b03dada0cac5692dc42a0962f0f708fe7e45c94c','select id,previous_version_id,title,summary,body,claims,topic_slugs,correction_note,status,created_at from post_versions where post_id=%s and (status=''approved'' or %s) order by created_at,id',true);
insert into agent_private.operation_catalog values ('dc42f5df857fa5bda7a7c20842f11d648232758c26629f584eae16787572f9e9','select * from story_clusters order by first_seen_at desc limit 100',true);
insert into agent_private.operation_catalog values ('e383174ef0bacf4aee4e85585cafb7f5d4cb2634b812ecbd425f11108e8adf9b','select * from agent_private.outbox where status=''pending'' order by created_at limit 1',true);
insert into agent_private.operation_catalog values ('e5fccfa47179c527dbdca2c2a5815152c538b13cfb2b538b3e6c98aa58173a3f','select p.id,p.published_at,p.current_version_id,v.title,v.summary,v.body,v.claims, v.correction_note,v.topic_slugs from posts p join post_versions v on v.id=p.current_version_id where p.id=%s and p.status=''published'' and v.status=''approved''',true);
insert into agent_private.operation_catalog values ('e8054dfa54930a071f4c7138bbef23f9ef0143d5cfffd64a882dd59e80efdeb0','update content_sources set failure_count=failure_count+1 where id=%s',false);
insert into agent_private.operation_catalog values ('ea9981cf5037fc42946a8004a84d7077b980d0fad6ba6f44017aaae1af5bb7c7','select id,role,content,created_at from messages where conversation_id=%s and user_id=%s order by created_at,id limit 500',true);
insert into agent_private.operation_catalog values ('ed08b9249ceb9aafce2f8d052fe706b329216707f923dfd703c5763d81046fec','select id,slug from topics where slug=any(%s) and is_active',true);
insert into agent_private.operation_catalog values ('ed8dffcf55804841eda4cb60cf2e9f84ffd43ae8892e0a0fbc41b30f5004606b','insert into agent_private.api_rate_windows(user_id,bucket,requests) values(%s,%s,1) on conflict(user_id,bucket) do update set requests=api_rate_windows.requests+1 returning requests',true);
insert into agent_private.operation_catalog values ('ee042b5063df6b2b694ca990e9ac347c0461d0c93dcc90ca5ea004ec2aa13462','select id from research_events where job_id=%s and user_id=%s and id>%s order by id limit 1',true);
insert into agent_private.operation_catalog values ('ee10456d553929472c7273d37360556c35c777a3313c9779b91e61dde348c39a','update post_versions set status=%s where id=%s',false);
insert into agent_private.operation_catalog values ('f17a4d342aebb89fc8828575e474f11fd31e2dd0b8ddc8854c6b9504a7978c54','insert into discovery_runs(query_id) values(%s) returning id',true);
insert into agent_private.operation_catalog values ('f85fbf96550eb78c457441fb604cc52dba1213bae53d6a7116e2f4a9bbf3b42d','select id from topics where id=%s and is_active',true);
insert into agent_private.schema_migrations(name) values ('20261011074408_supabase_data_api_runtime.sql') on conflict do nothing;
notify pgrst, 'reload schema';
