import pytest
from conftest import context

from app.agents.post.graph import build_post_graph
from app.core.errors import EvidenceError
from app.domain.models import Claim, Draft, Report, Source, Verification


async def test_unverified_research_cannot_be_drafted(settings):
    class LLM:
        async def complete(self, *args, **kwargs):
            pytest.fail("Unverified report must fail before a paid model call")

    ctx = context(settings, LLM())
    with pytest.raises(EvidenceError):
        await build_post_graph(ctx).ainvoke({"report": Report(answer="Unsupported").model_dump()})


async def test_post_draft_has_checked_citations_but_is_never_published(settings):
    source = Source(
        id="S1",
        url="https://example.org/release",
        title="Release",
        text="This release supports signed artifacts with verified build metadata.",
    )
    claim = Claim(text="Signed artifacts are supported", source_id="S1", quote=source.text)

    class LLM:
        async def complete(self, messages, tier="research", schema=None):
            if schema is Draft:
                return Draft(
                    title="Signed release artifacts",
                    summary="A release adds verified build metadata.",
                    body="The release supports signed artifacts with verified build metadata [S1].",
                    topic_slugs=["programming"],
                    claims=[claim],
                )
            return Verification(supported=[True], contradictions=[])

    ctx = context(settings, LLM())
    report = Report(
        answer="Signed artifacts [S1].", claims=[claim], sources=[source], verified=True
    )
    result = await build_post_graph(ctx).ainvoke({"report": report.model_dump()})
    assert "[S1](https://example.org/release)" in result["draft"]["body"]
    assert not any("insert into posts" in sql for sql, _ in ctx.repo.statements)


async def test_verified_flag_without_valid_evidence_cannot_trigger_generation(settings):
    class LLM:
        async def complete(self, *args, **kwargs):
            pytest.fail("Invalid evidence must fail before model generation")

    ctx = context(settings, LLM())
    with pytest.raises(EvidenceError):
        await build_post_graph(ctx).ainvoke(
            {"report": Report(answer="Unbacked verified flag", verified=True).model_dump()}
        )


@pytest.mark.parametrize(
    "summary",
    [
        "Unsupported reference in summary [S99].",
        "Injected source https://unverified.example/claim",
        "Injected source HTTPS://unverified.example/claim",
    ],
)
async def test_summary_cannot_bypass_server_citation_checks(settings, summary):
    source = Source(
        id="S1",
        url="https://example.org/release",
        title="Release",
        text="The release supports signed artifacts and verified build metadata.",
    )
    claim = Claim(text="Signed artifacts", source_id="S1", quote=source.text)

    class LLM:
        async def complete(self, messages, tier="research", schema=None):
            if schema is Draft:
                return Draft(
                    title="Signed artifacts",
                    summary=summary,
                    body="The release supports signed artifacts and verified build metadata [S1].",
                    topic_slugs=["programming"],
                    claims=[claim],
                )
            return Verification(supported=[True], contradictions=[])

    report = Report(answer="Signed artifacts", verified=True, sources=[source], claims=[claim])
    with pytest.raises(EvidenceError):
        await build_post_graph(context(settings, LLM())).ainvoke({"report": report.model_dump()})
