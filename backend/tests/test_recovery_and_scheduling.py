import pytest
from conftest import context
from langgraph.checkpoint.memory import InMemorySaver
from test_research_graph import Fetch, ResearchLLM, Search

from app.agents.chat.graph import build_graph
from app.domain.models import Plan
from app.workers.scheduling import schedule_research


async def test_checkpoint_resume_does_not_repeat_completed_search(settings):
    saver = InMemorySaver()
    search = Search()
    ctx = context(settings, ResearchLLM(), search=search, fetcher=Fetch())
    config = {"configurable": {"thread_id": "owner:job:1"}}
    graph = build_graph(ctx, saver)
    await graph.ainvoke(
        {"prompt": "Search the latest release"}, config, interrupt_after=["read_candidate"]
    )
    saved = await graph.aget_state(config)
    assert saved.next == ("assess",)
    resumed = context(settings, ResearchLLM(), search=search, fetcher=Fetch())
    resumed.job = ctx.job
    for key, value in saved.values["budget"].items():
        setattr(resumed.budget, key, value)
    report = await build_graph(resumed, saver).ainvoke(None, config)
    assert report["report"]["verified"]
    assert search.calls == 1
    assert resumed.budget.pages == 1


async def test_deep_budget_exhaustion_never_returns_unverified_factual_answer(settings):
    settings.deep_max_pages = 1

    class DeepLLM(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is Plan:
                return Plan(queries=["official release"])
            return await super().complete(messages, tier, schema)

    ctx = context(settings, DeepLLM(supported=False), search=Search(), fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Deep research the latest release"})
    assert not result["report"]["verified"]
    assert not result["report"]["claims"]
    assert "could not verify" in result["report"]["answer"]


async def test_scheduling_disabled_by_default_makes_no_queries(settings):
    class Repo:
        async def many(self, *args):
            pytest.fail("Disabled scheduler must not query")

    await schedule_research(settings, Repo())
