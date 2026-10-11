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
