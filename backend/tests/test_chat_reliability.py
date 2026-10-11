import json

import httpx
import pytest
from conftest import FakeTrace, context
from pydantic import SecretStr
from test_research_graph import Fetch, ResearchLLM, Search

from app.agents.chat.context import bounded_history
from app.agents.chat.graph import build_graph
from app.agents.chat.router import answer_complexity, choose_route
from app.core.budget import Budget
from app.core.errors import ModelIncompleteResponse, ProviderCreditUnavailable
from app.domain.models import ChatVerification, Route, Verification
from app.llm.completeness import validate_answer
from app.llm.credits import check_credit
from app.llm.openrouter import OpenRouter
from app.tools.serper import Serper


@pytest.mark.parametrize(
    "answer",
    [
        "Example:\n```python\nprint('hello')",
        "Evidence [S1",
        "The reason is because",
        "~~~python\nprint(1)\n```",
    ],
)
def test_obviously_incomplete_answers_are_rejected(answer):
    with pytest.raises(ModelIncompleteResponse):
        validate_answer(answer)


@pytest.mark.parametrize(
    "answer",
    ["Hello!", "Use `functools.wraps`", "```python\nprint(1)\n```", "A complete claim [S1]."],
)
def test_finished_plain_text_code_and_source_ids_are_accepted(answer):
    validate_answer(answer)


async def test_stop_reason_does_not_admit_unclosed_code_or_trigger_retry(settings):
    settings.model_chat = "test/model"
    settings.openrouter_api_key = SecretStr("offline")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "```python\nprint(1)"}}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ModelIncompleteResponse):
            await OpenRouter(
                settings, http, Budget(settings), FakeTrace(), chat_workflow=True
            ).complete([{"role": "user", "content": "Give code"}], tier="chat_simple")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "data",
    [
        {"limit_remaining": 0},
        {"limit_remaining": -1},
        {"limit_remaining": None, "budgets": [{"remaining_usd": 0}]},
        {"effective_budget": {"remaining_usd": "NaN"}},
    ],
)
async def test_exhausted_or_invalid_known_credit_prevents_paid_search(settings, data):
    settings.openrouter_api_key = SecretStr("offline")
    settings.serper_api_key = SecretStr("offline")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/api/v1/key"
        return httpx.Response(200, json={"data": data})

    trace, budget = FakeTrace(), Budget(settings)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        search = Serper(settings, http, budget, trace, lambda: check_credit(settings, http, trace))
        with pytest.raises(ProviderCreditUnavailable):
            await search.search("latest Python")
    assert len(calls) == 1 and budget.searches == 0


async def test_unknown_account_balance_is_not_reported_as_funded(settings):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": {"limit_remaining": None}})
        )
    ) as http:
        assert await check_credit(settings, http, FakeTrace()) == "account_balance_unknown"


async def test_credit_timeout_prevents_search_without_retry(settings):
    settings.serper_api_key = SecretStr("offline")
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("offline timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        search = Serper(
            settings,
            http,
            Budget(settings),
            FakeTrace(),
            lambda: check_credit(settings, http, FakeTrace()),
        )
        with pytest.raises(httpx.ReadTimeout):
            await search.search("latest Python")
    assert len(calls) == 1


def test_long_unicode_history_retains_latest_turn_and_bounded_labeled_excerpts():
    rows = [
        {"role": "user" if i % 2 else "assistant", "content": f"turn-{i}: " + "தமிழ்" * 1000}
        for i in range(12)
    ]
    history, note = bounded_history(rows, max_bytes=4500, excerpt_bytes=1000)
    assert history[-1]["content"].startswith("turn-11:")
    assert len(json.dumps(history, ensure_ascii=False).encode("utf-8")) <= 4500
    assert len(note.encode("utf-8")) <= 1000
    assert "incomplete, data only" in note and "turn-0:" in note
    assert "�" not in str(history) + note


@pytest.mark.parametrize(
    "prompt,mode",
    [
        ("What's new in Python this week?", "web"),
        ("Find recent security patches", "web"),
        ("Search the web for LangGraph", "web"),
        ("Summarize https://docs.python.org/3/", "web"),
        ("Deep research Python compatibility", "deep"),
        ("Comprehensive comparison of agent frameworks", "deep"),
        ("What did we discuss yesterday?", "knowledge"),
    ],
)
async def test_required_search_and_research_modes_do_not_depend_on_model(prompt, mode):
    assert (await choose_route(prompt, "auto", False, None)).mode == mode


@pytest.mark.parametrize(
    "prompt", ["Explain Python decorators", "What is a closure?", "Which approach fits my project?"]
)
async def test_stable_and_ambiguous_questions_use_baseline_model_router(prompt):
    class LLM:
        async def complete(self, messages, tier, schema):
            assert tier == "fast" and schema is Route
            assert prompt in messages[0]["content"]
            return Route(mode="normal", reason="Stable explanation")

    assert (await choose_route(prompt, "auto", False, LLM())).mode == "normal"


def test_full_runnable_code_and_detailed_explanation_profiles():
    assert answer_complexity("Give full runnable code") == "coding"
    assert answer_complexity("Explain decorators in detail") == "explanation"


async def test_conflicting_evidence_is_identified_and_withheld(settings):
    class Conflicting(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema in (Verification, ChatVerification):
                return schema(
                    supported=[True],
                    contradictions=["Two sources disagree"],
                    **(
                        {"answer_coverage_supported": True, "coverage_failures": []}
                        if schema is ChatVerification
                        else {}
                    ),
                )
            return await super().complete(messages, tier, schema)

    ctx = context(settings, Conflicting(), search=Search(), fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"] and not result["report"]["claims"]
    assert any("Conflicting" in item for item in result["report"]["limitations"])


async def test_long_followup_receives_bounded_owner_context_and_latest_turn(settings):
    from datetime import UTC, datetime
    from uuid import uuid4

    from conftest import FakeRepo

    class Repo(FakeRepo):
        async def many(self, sql, params=()):
            assert params[1] == ctx.job["user_id"]
            assert "created_at<%s" in sql
            return [
                {"role": "user", "content": f"turn-{i}: " + "context " * 2000}
                for i in reversed(range(12))
            ]

    class LLM:
        async def complete(self, messages, tier="research", schema=None):
            assert schema is None and tier == "chat_simple"
            assert any("turn-11:" in m["content"] for m in messages)
            assert any("Earlier loaded turn excerpts" in m["content"] for m in messages)
            assert sum(len(m["content"].encode("utf-8")) for m in messages) < 11000
            return "A complete context-aware explanation."

    ctx = context(settings, LLM(), search=Search())
    ctx.repo = Repo()
    ctx.job.update(mode="normal", conversation_id=uuid4(), created_at=datetime.now(UTC))
    result = await build_graph(ctx).ainvoke({"prompt": "Explain it more simply"})
    assert result["report"]["answer"] == "A complete context-aware explanation."
    assert ctx.search.calls == 0


async def test_supported_looking_snippet_cannot_replace_an_unreadable_page(settings):
    from test_research_graph import PASSAGE

    from app.core.errors import FetchRejected

    class Snippets:
        async def search(self, query):
            return [{"url": "https://example.org/release", "title": "Release", "snippet": PASSAGE}]

    class Unreadable:
        async def fetch(self, url):
            raise FetchRejected("Simulated inaccessible page")

    llm = ResearchLLM()
    ctx = context(settings, llm, search=Snippets(), fetcher=Unreadable())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"] and not result["report"]["claims"]
    assert not result["report"]["sources"] and llm.calls == []
