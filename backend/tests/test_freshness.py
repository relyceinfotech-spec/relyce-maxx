from datetime import date

from conftest import context
from test_research_graph import Fetch, ResearchLLM, Search

from app.agents.chat.graph import build_graph
from app.agents.chat.query import search_query


def test_this_week_search_uses_utc_calendar_week():
    query = search_query("What's new in Python this week? Verify sources.", date(2026, 10, 11))
    assert query == "What's new in Python this week after:2026-10-05 before:2026-10-12"
    assert search_query("Python decorators", date(2026, 10, 11)) == "Python decorators"


async def test_this_week_forces_search_without_classifier(settings):
    ctx = context(settings, ResearchLLM(), search=Search(), fetcher=Fetch())
    final = await build_graph(ctx).ainvoke({"prompt": "What's new in Python this week?"})
    assert final["route"]["mode"] == "web"
    assert 1 <= ctx.search.calls <= settings.chat_web_search_iterations


async def test_reading_a_url_does_not_call_serper(settings):
    ctx = context(settings, ResearchLLM(), search=Search(), fetcher=Fetch())
    final = await build_graph(ctx).ainvoke({"prompt": "Summarize https://docs.python.org/3/"})
    assert final["report"]["verified"]
    assert final["report"]["sources"][0]["url"] == "https://docs.python.org/3/"
    assert ctx.search.calls == 0
