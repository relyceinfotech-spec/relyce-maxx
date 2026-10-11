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
