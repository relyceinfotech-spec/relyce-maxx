-- Installed langgraph-checkpoint-postgres schema, private and transactional.
set local search_path to agent_private, public;
CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);
insert into checkpoint_migrations(v) values (0) on conflict do nothing;
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
insert into checkpoint_migrations(v) values (1) on conflict do nothing;
CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
insert into checkpoint_migrations(v) values (2) on conflict do nothing;
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
insert into checkpoint_migrations(v) values (3) on conflict do nothing;
ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;
insert into checkpoint_migrations(v) values (4) on conflict do nothing;
SELECT 1;
insert into checkpoint_migrations(v) values (5) on conflict do nothing;

    CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);

insert into checkpoint_migrations(v) values (6) on conflict do nothing;

    CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);

insert into checkpoint_migrations(v) values (7) on conflict do nothing;

    CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);

insert into checkpoint_migrations(v) values (8) on conflict do nothing;
ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';
insert into checkpoint_migrations(v) values (9) on conflict do nothing;
alter table agent_private.checkpoint_migrations enable row level security;
revoke all on agent_private.checkpoint_migrations from public,anon,authenticated;
alter table agent_private.checkpoints enable row level security;
revoke all on agent_private.checkpoints from public,anon,authenticated;
alter table agent_private.checkpoint_blobs enable row level security;
revoke all on agent_private.checkpoint_blobs from public,anon,authenticated;
alter table agent_private.checkpoint_writes enable row level security;
revoke all on agent_private.checkpoint_writes from public,anon,authenticated;
