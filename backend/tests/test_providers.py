import json

import httpx
import pytest
from conftest import FakeTrace
from pydantic import SecretStr, ValidationError

from app.core.budget import Budget
from app.core.errors import BudgetExceeded
from app.domain.models import Plan
from app.llm.openrouter import OpenRouter


async def test_structured_output_and_usage_are_validated(settings):
    settings.model_research = "test/model"
    settings.openrouter_api_key = SecretStr("test")
    trace = FakeTrace()

    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["strict"]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"queries":["official documentation"]}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "cost": 0.01},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await OpenRouter(settings, http, Budget(settings), trace).complete(
            [{"role": "user", "content": "Plan"}], schema=Plan
        )
    assert result.queries == ["official documentation"]
    assert trace.calls[0][1]["cost"] == 0.01


async def test_invalid_json_does_not_silently_retry(settings):
    from pydantic import SecretStr

    settings.model_research = "test/model"
    settings.openrouter_api_key = SecretStr("test")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"queries":[]}'}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ValidationError):
            await OpenRouter(settings, http, Budget(settings), FakeTrace()).complete(
                [{"role": "user", "content": "Plan"}], schema=Plan
            )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "chat_workflow,tier,deep,expected",
    [
        (True, "fast", False, 768),
        (True, "chat", False, 1200),
        (True, "chat_simple", False, 768),
        (True, "chat_explanation", False, 1200),
        (True, "chat_coding", False, 2000),
        (True, "research", False, 1800),
        (True, "research", True, 6000),
        (False, "research", True, 2400),
    ],
)
async def test_task_limit_matches_request_and_reservation(
    settings, chat_workflow, tier, deep, expected
):
    settings.openrouter_api_key = SecretStr("offline")
    setattr(settings, "model_chat" if tier.startswith("chat_") else f"model_{tier}", "test/model")
    budget = Budget(settings, deep=deep)

    def handler(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == expected
        assert (
            budget.tokens_reserved
            == len(json.dumps(body["messages"]).encode("utf-8")) + expected + 256
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Complete answer"}, "finish_reason": "stop"}]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        answer = await OpenRouter(
            settings, http, budget, FakeTrace(), chat_workflow=chat_workflow
        ).complete([{"role": "user", "content": "Explain decorators"}], tier=tier)
    assert answer == "Complete answer"


async def test_billing_error_does_not_shrink_limit_or_use_fallback(settings):
    settings.openrouter_api_key = SecretStr("offline")
    settings.model_chat = "test/model"
    settings.model_fallback = "test/fallback"
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await OpenRouter(
                settings, http, Budget(settings), FakeTrace(), chat_workflow=True
            ).complete([{"role": "user", "content": "Explain decorators"}], tier="chat_simple")
    assert len(calls) == 1 and calls[0]["max_tokens"] == 768


async def test_json_mode_includes_schema_and_still_rejects_invalid_output(settings):
    settings.model_research = "test/json-model"
    settings.openrouter_api_key = SecretStr("test")
    settings.structured_output_mode = "json_object"
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["response_format"] == {"type": "json_object"}
        assert '"minItems": 1' in body["messages"][0]["content"]
        content = '{"queries":["official docs"]}' if len(calls) == 1 else '{"queries":[]}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        llm = OpenRouter(settings, http, Budget(settings), FakeTrace())
        assert (await llm.complete([{"role": "user", "content": "Plan"}], schema=Plan)).queries == [
            "official docs"
        ]
        with pytest.raises(ValidationError):
            await llm.complete([{"role": "user", "content": "Plan"}], schema=Plan)
    assert len(calls) == 2


def test_budget_blocks_before_spending(settings):
    settings.max_run_cost_usd = 0.001
    budget = Budget(settings)
    with pytest.raises(BudgetExceeded):
        budget.reserve_model("input", 2000)
    assert budget.model_calls == 0


def test_page_and_search_limits(settings):
    settings.max_searches = 1
    settings.max_pages = 1
    budget = Budget(settings)
    budget.take_search()
    budget.take_page()
    with pytest.raises(BudgetExceeded):
        budget.take_search()
    with pytest.raises(BudgetExceeded):
        budget.take_page()


@pytest.mark.parametrize(
    "content,finish,error",
    [
        ("", "stop", "ModelEmptyResponse"),
        ('{"queries":["docs"]}', "length", "ModelOutputTruncated"),
    ],
)
async def test_empty_or_truncated_output_is_withheld_without_paid_retry(
    settings, content, finish, error
):
    import app.core.errors as errors

    settings.model_research = "test/model"
    settings.openrouter_api_key = SecretStr("offline")
    settings.model_reasoning_effort = "low"
    calls, trace = [], FakeTrace()

    def handler(request):
        calls.append(request)
        assert json.loads(request.content)["reasoning"] == {"effort": "low"}
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": finish, "message": {"content": content}}],
                "usage": {"completion_tokens": 2400, "cost": 0.01},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(getattr(errors, error)):
            await OpenRouter(settings, http, Budget(settings), trace).complete(
                [{"role": "user", "content": "Plan"}], schema=Plan
            )
    assert len(calls) == 1
    assert trace.calls[0][1]["cost"] == 0.01
