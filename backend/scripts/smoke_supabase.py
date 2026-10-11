"""Live API and durable-checkpoint smoke; no model calls or user records."""

import asyncio
import json
from typing import TypedDict
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph

from app.core.config import Settings
from app.database.checkpoints import SupabaseSaver
from app.database.supabase import SupabaseRepository
from app.main import create_app


class State(TypedDict):
    count: int


async def checkpoints(settings):
    thread_id = "smoke-checkpoint:" + str(uuid4())
    async with httpx.AsyncClient(timeout=20, trust_env=False) as http:
        repo = SupabaseRepository(settings, http)

        def graph():
            builder = StateGraph(State)
            builder.add_node("first", lambda state: {"count": state["count"] + 1})
            builder.add_node("second", lambda state: {"count": state["count"] + 1})
            builder.add_edge(START, "first")
            builder.add_edge("first", "second")
            builder.add_edge("second", END)
            return builder.compile(checkpointer=SupabaseSaver(repo), interrupt_before=["second"])

        config = {"configurable": {"thread_id": thread_id}}
        try:
            initial = await graph().ainvoke({"count": 0}, config)
            assert initial["count"] == 1
            restarted = graph()
            saved = await restarted.aget_state(config)
            assert saved.next == ("second",)
            result = await restarted.ainvoke(None, config)
            assert result["count"] == 2
            history = [item async for item in SupabaseSaver(repo).alist(config)]
            assert len(history) >= 3
            print(
                json.dumps({"durable_checkpoint_restart": "passed", "history_count": len(history)})
            )
        finally:
            saver = SupabaseSaver(repo)
            await saver.adelete_thread(thread_id)
            assert await saver.aget_tuple(config) is None
            print(json.dumps({"checkpoint_cleanup": "passed"}))


def main():
    settings = Settings()
    assert settings.database_transport == "supabase"
    assert not settings.missing(), "Required configuration is missing"
    with TestClient(create_app(settings)) as client:
        for path in ("/health", "/ready", "/api/topics"):
            response = client.get(path)
            assert response.status_code == 200, f"{path} returned {response.status_code}"
            print(json.dumps({"route": path, "status": response.status_code}))
        assert client.post("/api/chat", json={}).status_code == 401
    asyncio.run(checkpoints(settings))


if __name__ == "__main__":
    main()
