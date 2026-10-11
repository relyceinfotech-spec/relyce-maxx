from uuid import uuid4

import pytest
from conftest import FakeRepo

from app.agents.chat.router import answer_complexity, choose_route
from app.observability.tracing import Trace


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("Explain decorators in approximately 150 words with one small example", "simple"),
        ("Explain it more simply", "simple"),
        ("Explain decorators and functools.wraps", "explanation"),
        ("Implement a complete decorator with tests", "coding"),
    ],
)
def test_complexity_respects_short_requests_without_changing_search_mode(prompt, expected):
    assert answer_complexity(prompt) == expected


async def test_brief_fresh_request_still_requires_web():
    prompt = "Briefly explain the latest Python news"
    assert answer_complexity(prompt) == "simple"
    assert (await choose_route(prompt, "auto", False, None)).mode == "web"


async def test_output_metrics_exclude_raw_answer():
    events = []

    class Repo(FakeRepo):
        async def event(self, job, stage, data):
            events.append((stage, data))

    trace = Trace(Repo(), uuid4())
    trace.job = {"id": uuid4()}
    await trace.model(
        "test/model",
        {
            "completion_tokens": 192,
            "output_ceiling": 768,
            "finish_reason": "stop",
            "raw_content": "private answer",
        },
        100,
        0.01,
        "completed",
    )
    assert events[0][0] == "model_output"
    assert events[0][1]["ceiling_utilization"] == 0.25
    assert events[0][1]["finish_reason"] == "stop"
    assert "private answer" not in str(events)
