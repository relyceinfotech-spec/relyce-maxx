"""Durable LangGraph checkpoints over service-only Supabase RPC."""

from contextlib import asynccontextmanager

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_id,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class SupabaseSaver(BaseCheckpointSaver):
    def __init__(self, repo):
        super().__init__(serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
        self.repo = repo

    def dump(self, value):
        kind, data = self.serde.dumps_typed(value)
        return {"type": kind, "data": data.hex()}

    def load(self, value):
        return self.serde.loads_typed((value["type"], bytes.fromhex(value["data"])))

    async def request(self, operation, payload):
        return await self.repo.rpc("maxxx_checkpoint", {"operation": operation, "payload": payload})

    async def aget_tuple(self, config):
        cfg = config["configurable"]
        rows = await self.request(
            "get",
            {
                "thread_id": cfg["thread_id"],
                "ns": cfg.get("checkpoint_ns", ""),
                "id": get_checkpoint_id(config),
            },
        )
        return self.tuple(rows[0]) if rows else None

    def tuple(self, row):
        checkpoint = self.load(row["checkpoint"])
        checkpoint["channel_values"] = {
            blob["channel"]: self.load(blob["value"])
            for blob in row["blobs"]
            if blob["value"]["type"] != "empty"
        }
        cfg = {
            "thread_id": row["thread_id"],
            "checkpoint_ns": row["ns"],
            "checkpoint_id": row["id"],
        }
        parent = (
            {"configurable": {**cfg, "checkpoint_id": row["parent"]}} if row["parent"] else None
        )
        return CheckpointTuple(
            config={"configurable": cfg},
            checkpoint=checkpoint,
            metadata=self.load(row["metadata"]),
            parent_config=parent,
            pending_writes=[
                (w["task_id"], w["channel"], self.load(w["value"])) for w in row["writes"]
            ],
        )

    async def alist(self, config, *, filter=None, before=None, limit=None):
        cfg = config["configurable"] if config else {}
        rows = await self.request(
            "list",
            {
                "thread_id": cfg.get("thread_id"),
                "ns": cfg.get("checkpoint_ns"),
                "before": get_checkpoint_id(before) if before else None,
                "filter": filter or {},
                "limit": min(limit if limit is not None else 100, 1000),
            },
        )
        for row in rows:
            yield self.tuple(row)

    async def aput(self, config, checkpoint, metadata, new_versions):
        cfg = config["configurable"]
        copy = {**checkpoint, "channel_values": {}}
        blobs = [
            {
                "channel": channel,
                "version": str(version),
                "value": self.dump(checkpoint["channel_values"][channel])
                if channel in checkpoint["channel_values"]
                else {"type": "empty", "data": ""},
            }
            for channel, version in new_versions.items()
        ]
        await self.request(
            "put",
            {
                "thread_id": cfg["thread_id"],
                "ns": cfg.get("checkpoint_ns", ""),
                "id": checkpoint["id"],
                "parent": cfg.get("checkpoint_id"),
                "checkpoint": self.dump(copy),
                "metadata": self.dump(metadata),
                "metadata_filter": metadata,
                "versions": {k: str(v) for k, v in checkpoint["channel_versions"].items()},
                "blobs": blobs,
            },
        )
        return {
            "configurable": {
                "thread_id": cfg["thread_id"],
                "checkpoint_ns": cfg.get("checkpoint_ns", ""),
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def aput_writes(self, config, writes, task_id, task_path=""):
        cfg = config["configurable"]
        await self.request(
            "writes",
            {
                "thread_id": cfg["thread_id"],
                "ns": cfg.get("checkpoint_ns", ""),
                "id": cfg["checkpoint_id"],
                "task_id": task_id,
                "task_path": task_path,
                "writes": [
                    {
                        "idx": WRITES_IDX_MAP.get(channel, idx),
                        "channel": channel,
                        "value": self.dump(value),
                    }
                    for idx, (channel, value) in enumerate(writes)
                ],
            },
        )

    async def adelete_thread(self, thread_id):
        await self.request("delete", {"thread_id": thread_id})


@asynccontextmanager
async def checkpointer(repo):
    if hasattr(repo, "command"):
        yield SupabaseSaver(repo)
        return
    async with repo.pool.connection() as conn:
        await conn.execute("set search_path to agent_private,public")
        try:
            yield AsyncPostgresSaver(conn, serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
        finally:
            await conn.execute("reset search_path")
