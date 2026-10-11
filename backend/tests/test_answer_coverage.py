"""Offline controller cases; captured passages do not recover the lost live draft."""

import json
from pathlib import Path

import pytest
from conftest import context
from test_research_graph import PASSAGE, Fetch, ResearchLLM, Search

from app.agents.chat.graph import build_graph
from app.core.errors import EvidenceError
from app.domain.models import ChatReport, ChatVerification, PassageClaim, VerificationIssue
from app.research.coverage import answer_sentences, validate_sentence_claims

CAPTURED = json.loads(
    (Path(__file__).parent / "fixtures/chat_coverage_20261011.json").read_text(encoding="utf-8")
)


def report(answer="Signed artifacts are provided [S1].", mappings=None):
    return ChatReport(
        answer=answer,
        claims=[
            PassageClaim(
                text="Signed artifacts are provided",
                source_id="S1",
                passage_id="S1-P1",
                quote=PASSAGE,
            )
        ],
        sentence_claims=mappings or [{"sentence_index": 0, "claim_indices": [0]}],
    )


def test_versions_citations_and_blank_lines_keep_contiguous_indexes():
    units = answer_sentences("Python 3.15 is available. [S1].\n\nArtifacts are signed [S2].")
    assert [u["sentence_index"] for u in units] == [0, 1]
    assert units[0]["text"] == "Python 3.15 is available. [S1]."


@pytest.mark.parametrize(
    "answer,mappings",
    [
        ("Signed artifacts are provided.", [{"sentence_index": 0, "claim_indices": [0]}]),
        ("Signed artifacts [S2].", [{"sentence_index": 0, "claim_indices": [0]}]),
        (
            "Signed artifacts [S1]. Another unsupported fact [S1].",
            [{"sentence_index": 0, "claim_indices": [0]}],
        ),
        ("Signed artifacts [S1].", [{"sentence_index": 0, "claim_indices": [99]}]),
        (
            "Signed artifacts [S1].",
            [
                {"sentence_index": 0, "claim_indices": [0]},
                {"sentence_index": 0, "claim_indices": [0]},
            ],
        ),
    ],
)
def test_missing_or_invalid_sentence_mappings_fail_before_semantic_call(answer, mappings):
    with pytest.raises(EvidenceError):
        validate_sentence_claims(report(answer, mappings), required=True)


async def test_new_unmapped_factual_sentence_is_withheld_and_indexed(settings):
    class ExtraFact(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            result = await super().complete(messages, tier, schema)
            if schema is ChatReport:
                result.answer += " Another unsupported fact [S1]."
            return result

    ctx = context(settings, ExtraFact(), search=Search(), fetcher=Fetch())
    diagnostics = []
    ctx.evidence_diagnostics = diagnostics.append
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"]
    assert ChatVerification not in ctx.llm.calls
    assert diagnostics[-1]["sentence_issues"][0]["sentence_indices"] == [1]
    assert "unsupported fact" not in str(diagnostics)


@pytest.mark.parametrize(
    "category", ["temporal_mismatch", "contradiction", "incomplete_coverage", "unsupported_scope"]
)
async def test_typed_issue_blocks_even_when_claim_booleans_and_coverage_flag_are_true(
    settings, category
):
    class Issue(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatVerification:
                return ChatVerification(
                    supported=[True],
                    contradictions=[],
                    answer_coverage_supported=True,
                    coverage_failures=[],
                    issues=[
                        VerificationIssue(
                            category=category,
                            sentence_indices=[0],
                            claim_indices=[0],
                            passage_ids=["S1-P1"],
                        )
                    ],
                )
            return await super().complete(messages, tier, schema)

    ctx = context(settings, Issue(), search=Search(), fetcher=Fetch())
    diagnostics = []
    ctx.evidence_diagnostics = diagnostics.append
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"]
    issue = next(d for d in diagnostics if d["event"] == "verification_result")["sentence_issues"][
        0
    ]
    assert issue["category"] == category and issue["passage_ids"] == ["S1-P1"]


@pytest.mark.parametrize("legacy_conflict", [False, True])
async def test_captured_jit_qualification_and_limited_scope_need_all_verification_gates(
    settings, legacy_conflict
):
    first, qualification = CAPTURED["passages"][:2]

    class CapturedFetch:
        async def fetch(self, url):
            return (
                url,
                200,
                {"content-type": "text/plain"},
                (first["text"] + "\n" + qualification["text"]).encode(),
            )

    class Limited(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatReport:
                # Synthetic limited candidate built from retained evidence, not the lost answer.
                return ChatReport(
                    answer="Coverage highlights JIT improvements [S1].\nJIT support remains conditional on performance guidelines [S1].",
                    claims=[
                        PassageClaim(
                            text="Coverage highlights JIT improvements",
                            source_id="S1",
                            passage_id="S1-P1",
                            quote=first["text"],
                        ),
                        PassageClaim(
                            text="JIT support remains conditional on performance guidelines",
                            source_id="S1",
                            passage_id="S1-P2",
                            quote=qualification["text"],
                        ),
                    ],
                    sentence_claims=[
                        {"sentence_index": 0, "claim_indices": [0]},
                        {"sentence_index": 1, "claim_indices": [1]},
                    ],
                    limitations=[
                        "Partial retrieved findings; no comprehensive weekly roundup or exact event date established."
                    ],
                )
            if schema is ChatVerification:
                assert "Do NOT classify missing coverage" in messages[0]["content"]
                data = json.loads(messages[0]["content"].split("\n", 1)[1])
                assert len(data["answer_sentences"]) == 2 and data["limitations"]
                return ChatVerification(
                    supported=[True, True],
                    contradictions=["A genuine unresolved conflict"] if legacy_conflict else [],
                    answer_coverage_supported=True,
                    coverage_failures=[],
                    issues=[
                        VerificationIssue(
                            category="qualification",
                            sentence_indices=[1],
                            claim_indices=[1],
                            passage_ids=["S1-P2"],
                        )
                    ],
                )
            return await super().complete(messages, tier, schema)

    result = await build_graph(
        context(settings, Limited(), search=Search(), fetcher=CapturedFetch())
    ).ainvoke({"prompt": "Search Python JIT support"})
    assert result["report"]["verified"] == (not legacy_conflict)
    assert len(result["report"]["citations"]) == (0 if legacy_conflict else 2)


def test_captured_run_counts_do_not_recover_missing_original_sentences():
    assert CAPTURED["retained_verification"]["supported"] == [True] * 5
    assert CAPTURED["retained_verification"]["coverage_failure_count"] == 2
    assert not CAPTURED["reconstructed_original_answer"]
    assert CAPTURED["document_dates"]["S3"] is None


async def test_unknown_verifier_passage_reference_fails_without_leaking_text(settings):
    class Invalid(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatVerification:
                return ChatVerification(
                    supported=[True],
                    contradictions=[],
                    answer_coverage_supported=True,
                    coverage_failures=[],
                    issues=[
                        VerificationIssue(
                            category="qualification",
                            sentence_indices=[0],
                            passage_ids=["private-placeholder-invalid-id"],
                        )
                    ],
                )
            return await super().complete(messages, tier, schema)

    ctx = context(settings, Invalid(), search=Search(), fetcher=Fetch())
    diagnostics = []
    ctx.evidence_diagnostics = diagnostics.append
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"]
    assert "private-placeholder-invalid-id" not in str(diagnostics)
