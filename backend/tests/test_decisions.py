import json

import httpx
import pytest
from conftest import FakeTrace, context
from pydantic import SecretStr, ValidationError

from app.agents.chat.graph import build_graph
from app.core.budget import Budget
from app.llm.decisions import Choice, Decisions
from scripts.eval_chat_routes import load_cases, metrics


def result(choice="web", confidence=0.9):
    return {
        "type": "choice",
        "choice": choice,
        "confidence": confidence,
        "probabilities": {"web": 0.85, "normal": 0.05, "knowledge": 0.05, "deep": 0.05},
    }


async def test_decisions_api_and_usage_are_separate_from_chat(settings):
    settings.openrouter_api_key = SecretStr("offline")
    trace = FakeTrace()

    def handler(request):
        assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
        payload = json.loads(request.content)
        assert payload["model"] == "perplexity/pplx-decider-v1.1-27b"
        assert payload["questions"]["decision"]["type"] == "choice"
        assert "messages" not in payload
        return httpx.Response(
            200,
            json={
                "answers": {"decision": result()},
                "usage": {"input_tokens": 20, "output_tokens": 4, "cost": 0.00001},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        prediction = await Decisions(settings, http, Budget(settings), trace).decide(
            "Latest Python updates"
        )
    assert prediction.choice == "web" and prediction.confidence == 0.9
    assert trace.calls[0][1] == {"prompt_tokens": 20, "completion_tokens": 4, "cost": 0.00001}


@pytest.mark.parametrize(
    "mutation",
    [
        {"choice": "publish"},
        {"confidence": float("nan")},
        {"confidence": 1.5},
        {"probabilities": {"web": 1}},
        {"probabilities": {"web": 1, "normal": 1, "knowledge": 0, "deep": 0}},
    ],
)
def test_invalid_decision_is_rejected(mutation):
    with pytest.raises(ValidationError):
        Choice.model_validate({**result(), **mutation})


@pytest.mark.parametrize("prediction", ["wrong", "low", "unavailable"])
async def test_shadow_never_overrides_authoritative_user_route(settings, prediction):
    class Model:
        async def complete(self, *args, **kwargs):
            assert kwargs.get("schema") is None
            return "A closure retains variables from its lexical scope."

    class Decision:
        async def decide(self, *args):
            if prediction == "unavailable":
                raise httpx.ConnectError("Offline provider")
            return Choice.model_validate(result(confidence=0.2 if prediction == "low" else 0.9))

    ctx = context(settings, Model())
    ctx.job["mode"] = "normal"
    ctx.decisions = Decision()
    final = await build_graph(ctx).ainvoke({"prompt": "Explain JavaScript closures"})
    assert final["route"]["mode"] == "normal"
    assert any(stage == "decision_shadow" for stage, _ in ctx.events)


def test_dataset_has_100_balanced_manually_labeled_cases():
    cases = load_cases()
    assert len(cases) == 100
    assert len([r for r in cases if r["requested"] != "auto"]) == 4
    # Empty/live-incomplete results must not be presented as a successful benchmark.
    assert metrics([], "jev")["accuracy"] is None
    values = metrics(
        [{"expected": "web", "jev": "normal"}, {"expected": "normal", "jev": "web"}], "jev"
    )
    assert values["missed_search_rate"] == values["unnecessary_search_rate"] == 1
    assert len(load_cases("discovery")) == 100
    assert len(load_cases("tricky")) == 50


@pytest.mark.parametrize("status", [402, 429, 503])
async def test_fallback_is_bounded_and_never_retries_credit_errors(settings, status):
    settings.openrouter_api_key = SecretStr("offline")
    models = []

    def handler(request):
        models.append(json.loads(request.content)["model"])
        return (
            httpx.Response(status)
            if len(models) == 1
            else httpx.Response(200, json={"answers": {"decision": result()}})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        gateway = Decisions(settings, http, Budget(settings), FakeTrace())
        if status == 503:
            assert (await gateway.decide("Latest news")).choice == "web"
            assert models == ["perplexity/pplx-decider-v1.1-27b", "typesafe/jev-1.13"]
        else:
            with pytest.raises(httpx.HTTPStatusError):
                await gateway.decide("Latest news")
            assert len(models) == 1
