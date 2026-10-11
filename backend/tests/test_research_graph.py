from conftest import context

from app.agents.chat.graph import build_graph
from app.domain.models import (
    ChatReport,
    ChatVerification,
    PassageClaim,
    Report,
    Source,
    Verification,
)

PASSAGE = "The software release includes signed artifacts and verified build metadata."


class ResearchLLM:
    def __init__(self, supported=True, fabricated=False):
        self.supported, self.fabricated = supported, fabricated
        self.calls = []

    async def complete(self, messages, tier="research", schema=None):
        self.calls.append(schema)
        if schema in (Verification, ChatVerification):
            return schema(
                supported=[self.supported],
                contradictions=[],
                **(
                    {"answer_coverage_supported": True, "coverage_failures": []}
                    if schema is ChatVerification
                    else {}
                ),
            )
        if schema in (Report, ChatReport):
            return schema(
                answer="Signed artifacts are provided [S1].",
                sentence_claims=[{"sentence_index": 0, "claim_indices": [0]}],
                claims=[
                    PassageClaim(
                        text="Signed artifacts are provided",
                        source_id="S99" if self.fabricated else "S1",
                        quote=PASSAGE,
                        passage_id="S1-P1",
                    )
                ],
            )
        return "Hello!"


class Search:
    def __init__(self):
        self.calls = 0

    async def search(self, query):
        self.calls += 1
        return [{"url": "https://example.org/release", "title": "Release"}]


class Fetch:
    async def fetch(self, url):
        return url, 200, {"content-type": "text/plain"}, (PASSAGE + " " + PASSAGE).encode()


async def test_normal_chat_never_searches(settings):
    llm, search = ResearchLLM(), Search()
    ctx = context(settings, llm, search=search)
    result = await build_graph(ctx).ainvoke({"prompt": "hi"})
    assert result["report"]["answer"] == "Hello!"
    assert not result["report"]["verified"]
    assert search.calls == 0
    assert llm.calls == [None]


async def test_web_research_produces_validated_server_citations(settings):
    ctx = context(settings, ResearchLLM(), search=Search(), fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert result["report"]["verified"]
    assert "[S1](https://example.org/release)" in result["report"]["answer"]
    assert {stage for stage, _ in ctx.events} >= {"routing", "searching", "reading", "verifying"}


async def test_rejected_claim_is_withheld(settings):
    ctx = context(settings, ResearchLLM(supported=False), search=Search(), fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"]
    assert not result["report"]["claims"]
    assert "could not verify" in result["report"]["answer"]


async def test_fabricated_source_cannot_pass(settings):
    ctx = context(settings, ResearchLLM(fabricated=True), search=Search(), fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"]


async def test_snippets_are_never_treated_as_evidence(settings):
    from app.core.errors import FetchRejected

    class Unreadable:
        async def fetch(self, url):
            raise FetchRejected("Source inaccessible")

    ctx = context(settings, ResearchLLM(), search=Search(), fetcher=Unreadable())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["sources"]
    assert not result["report"]["verified"]
    assert not ctx.llm.calls


async def test_knowledge_retrieval_receives_only_owner_id(settings):
    class Knowledge:
        async def recall(self, user_id, prompt, before):
            return []

        async def search(self, user_id, prompt):
            self.owner = user_id
            return [
                Source(id="K1", url="https://example.org/release", title="Release", text=PASSAGE)
            ]

    class KnowledgeLLM(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatReport:
                return ChatReport(
                    answer="Signed artifacts [K1].",
                    sentence_claims=[{"sentence_index": 0, "claim_indices": [0]}],
                    claims=[
                        PassageClaim(
                            text="Signed artifacts",
                            source_id="K1",
                            passage_id="K1-P1",
                            quote=PASSAGE,
                        )
                    ],
                )
            return await super().complete(messages, tier, schema)

    knowledge = Knowledge()
    ctx = context(settings, KnowledgeLLM(), knowledge=knowledge)
    result = await build_graph(ctx).ainvoke({"prompt": "What did we discuss yesterday?"})
    assert knowledge.owner == ctx.job["user_id"]
    assert result["report"]["verified"]
