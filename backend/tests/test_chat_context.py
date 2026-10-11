from datetime import UTC, datetime
from uuid import uuid4

from conftest import FakeRepo, context
from test_research_graph import PASSAGE, Fetch, ResearchLLM, Search

from app.agents.chat.context import load_context
from app.agents.chat.graph import build_graph
from app.domain.models import (
    ChatReport,
    ChatVerification,
    PassageClaim,
    Plan,
    Route,
    Source,
    Verification,
)


async def test_context_is_owner_scoped_and_stops_before_current_turn():
    owner, conversation = uuid4(), uuid4()
    cutoff = datetime.now(UTC)

    class Repo(FakeRepo):
        async def many(self, sql, params=()):
            assert params == (conversation, owner, cutoff)
            assert "created_at<%s" in sql
            return [
                {"role": "assistant", "content": "Previous answer"},
                {"role": "user", "content": "Previous question"},
            ]

    state = await load_context(
        Repo(), {"user_id": owner, "conversation_id": conversation, "created_at": cutoff}
    )
    assert [m["content"] for m in state["history"]] == ["Previous question", "Previous answer"]


async def test_normal_followup_keeps_prior_turn_and_sends_current_prompt_once(settings):
    class Repo(FakeRepo):
        async def many(self, sql, params=()):
            return [{"role": "user", "content": "Explain signed release artifacts"}]

    class LLM(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            assert schema is None
            assert "concise and complete" in messages[0]["content"]
            assert tier == "chat_simple"
            assert messages[1:] == [
                {"role": "user", "content": "Explain signed release artifacts"},
                {"role": "user", "content": "Explain it more simply"},
            ]
            return "A signature helps you check where a release came from."

    ctx = context(settings, LLM(), search=Search())
    ctx.job.update(mode="normal", conversation_id=uuid4(), created_at=datetime.now(UTC))
    ctx.repo = Repo()
    result = await build_graph(ctx).ainvoke({"prompt": "Explain it more simply"})
    assert result["report"]["answer"].startswith("A signature")
    assert ctx.search.calls == 0


async def test_web_followup_routes_and_searches_with_subject_context(settings):
    class Repo(FakeRepo):
        async def many(self, sql, params=()):
            return [{"role": "user", "content": "Tell me about Python release artifacts"}]

    class LLM(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is Route:
                assert "Python release artifacts" in messages[-1]["content"]
                return Route(mode="web", reason="Follow-up requires sources")
            if schema is Plan:
                assert "Python release artifacts" in messages[-1]["content"]
                return Plan(
                    queries=["Python release artifact verification official docs", "ignored"]
                )
            return await super().complete(messages, tier, schema)

    class SearchWithSubject(Search):
        async def search(self, query):
            assert query == "Python release artifact verification official docs"
            return await super().search(query)

    ctx = context(settings, LLM(), search=SearchWithSubject(), fetcher=Fetch())
    ctx.job.update(conversation_id=uuid4(), created_at=datetime.now(UTC))
    ctx.repo = Repo()
    result = await build_graph(ctx).ainvoke({"prompt": "How can I verify those?"})
    assert result["report"]["verified"]
    assert ctx.search.calls == 1


async def test_selected_post_body_is_context_but_source_passages_are_evidence(settings):
    class Repo(FakeRepo):
        async def one(self, sql, params=()):
            assert "p.status='published'" in sql and "v.status='approved'" in sql
            return {
                "title": "Release artifacts",
                "summary": "Signed software releases",
                "body": "Editorial discussion of release artifacts.",
                "sources": [
                    Source(
                        id="S1", url="https://example.org/release", title="Release", text=PASSAGE
                    ).model_dump()
                ],
            }

    class Knowledge:
        async def search(self, *args):
            return []

        async def recall(self, *args):
            return []

    class LLM:
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatReport:
                assert "Editorial discussion" in messages[0]["content"]
                return ChatReport(
                    answer="Artifacts are signed [P1].",
                    sentence_claims=[{"sentence_index": 0, "claim_indices": [0]}],
                    claims=[
                        PassageClaim(
                            text="Artifacts are signed",
                            source_id="P1",
                            passage_id="P1-P1",
                            quote=PASSAGE,
                        )
                    ],
                )
            assert schema in (Verification, ChatVerification)
            assert "Editorial discussion" not in messages[-1]["content"]
            return schema(
                supported=[True],
                contradictions=[],
                **(
                    {"answer_coverage_supported": True, "coverage_failures": []}
                    if schema is ChatVerification
                    else {}
                ),
            )

    ctx = context(settings, LLM(), knowledge=Knowledge())
    ctx.job.update(mode="knowledge", post_id=uuid4())
    ctx.repo = Repo()
    result = await build_graph(ctx).ainvoke({"prompt": "Explain this article"})
    assert result["report"]["verified"]
    assert "[P1](https://example.org/release)" in result["report"]["answer"]
