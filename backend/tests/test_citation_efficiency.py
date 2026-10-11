import json
from pathlib import Path

import pytest
from conftest import context
from test_research_graph import PASSAGE, Fetch, ResearchLLM, Search

from app.agents.chat.graph import build_graph
from app.core.errors import EvidenceError, FetchRejected
from app.domain.models import ChatReport, ChatVerification, PassageClaim, Source
from app.observability.usage import usage_summary
from app.research.content import boilerplate_only
from app.research.coverage import attach_sentence_citations, validate_sentence_claims
from app.research.evidence import numbered_passages, verification_payload
from app.tools.reader import PageReader

CAPTURED = json.loads(
    (Path(__file__).parent / "fixtures/chat_citations_20261011.json").read_text(encoding="utf-8")
)


def candidate(answer):
    return ChatReport(
        answer=answer,
        claims=[
            PassageClaim(
                text="Artifacts are signed", source_id="S1", passage_id="S1-P1", quote=PASSAGE
            )
        ],
        sentence_claims=[{"sentence_index": 0, "claim_indices": [0]}],
    )


@pytest.mark.parametrize(
    "answer", ["Artifacts are signed.", "Artifacts are signed [S1].", "Artifacts are signed. [S1]."]
)
def test_server_generates_same_marker_from_validated_mapping(answer):
    report = candidate(answer)
    attach_sentence_citations(report)
    assert report.answer == "Artifacts are signed [S1]."
    validate_sentence_claims(report, required=True)
    attach_sentence_citations(report)
    assert report.answer == "Artifacts are signed [S1]."


@pytest.mark.parametrize(
    "answer",
    [
        "Artifacts [S99].",
        "Artifacts https://evil.example/.",
        "Artifacts [S1](https://evil.example/).",
    ],
)
def test_unknown_or_model_generated_links_still_fail(answer):
    with pytest.raises(EvidenceError):
        attach_sentence_citations(candidate(answer))


async def test_uncited_draft_is_cited_before_independent_model_call(settings):
    class NoMarkers(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatVerification:
                data = json.loads(messages[0]["content"].split("\n", 1)[1])
                assert "[S1]" in data["answer"]
            result = await super().complete(messages, tier, schema)
            if schema is ChatReport:
                result.answer = "Signed artifacts are provided."
            return result

    result = await build_graph(
        context(settings, NoMarkers(), search=Search(), fetcher=Fetch())
    ).ainvoke({"prompt": "Search the latest release"})
    assert result["report"]["verified"] and result["report"]["citations"]


async def test_server_marker_does_not_override_failed_entailment(settings):
    ctx = context(settings, ResearchLLM(supported=False), search=Search(), fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"] and not result["report"]["citations"]


async def test_captured_footer_is_rejected_before_caching(settings):
    text = CAPTURED["footer_source"]["text"]
    assert boilerplate_only(text)

    class Footer(Fetch):
        async def fetch(self, url):
            return url, 200, {"content-type": "text/plain"}, text.encode()

    reader = PageReader(context(settings, ResearchLLM(), fetcher=Footer()))
    with pytest.raises(FetchRejected, match="footer-only"):
        await reader.open_page("https://example.org/footer")
    assert not reader.pages
    source = Source.model_validate(CAPTURED["footer_source"])
    assert numbered_passages([source.model_dump()], "Python news") == ([], [])


def test_content_with_footer_and_meaningful_article_is_preserved():
    assert not boilerplate_only(CAPTURED["footer_source"]["text"] + "\n" + PASSAGE)
    assert not boilerplate_only(
        "Privacy policy changes require companies to update their retention procedures."
    )


def test_duplicate_passages_keep_first_exact_offsets_and_source_ids():
    source = Source(
        id="S1", url="https://example.org/", title="Release", text=PASSAGE + "\n" + PASSAGE
    )
    evidence, selected = numbered_passages([source.model_dump()], "signed artifacts")
    assert len(selected) == 1 and selected[0]["passage_id"] == "S1-P1"
    p = selected[0]
    assert source.text[p["start_offset"] : p["end_offset"]] == p["text"]
    assert evidence[0]["passages"] == selected


def test_verifier_context_compaction_preserves_qualifications():
    text = PASSAGE + " The improvement is conditional and remains experimental."
    source = Source(id="S1", url="https://example.org/", title="Release", text=text)
    claims = [candidate("Claim.").claims[0]] * 2
    original = verification_payload("Claim [S1].", claims, [source])
    compact = verification_payload("Claim [S1].", claims, [source], compact=True)
    assert len(original["evidence"][0]["passages"]) == 2
    assert compact["evidence"][0]["passages"] == [text]
    assert len(json.dumps(compact)) < len(json.dumps(original))


def test_recorded_usage_is_separate_from_capacity_and_search_cost():
    summary = usage_summary(CAPTURED["model_usage"], CAPTURED["budget"])
    assert summary["reported_input_tokens"] == 3526
    assert summary["reported_output_tokens"] == 1352
    assert summary["reported_model_cost_usd"] == "0.0011166"
    assert summary["reserved_tokens"] == 20838
    assert summary["reserved_model_cost_usd"] == 1.0419
    assert summary["reported_cost_complete"] and not summary["search_cost_included"]
    assert not CAPTURED["original_draft_retained"]


def test_unreported_cost_is_not_presented_as_zero_billing():
    summary = usage_summary(
        [{"prompt_tokens": 1, "completion_tokens": 2, "reported_cost": None}], {}
    )
    assert summary["reported_model_cost_usd"] is None and not summary["reported_cost_complete"]
