from app.domain.models import Claim, Report, Source
from app.research.audit import audit_evidence
from scripts.audit_chat_evidence import audit_artifact

PASSAGE = "The asyncio library provides tools for writing concurrent code using async/await syntax."


def sample():
    source = Source(
        id="S1", url="https://docs.python.org/3/library/asyncio.html", title="asyncio", text=PASSAGE
    )
    report = Report(
        answer=f"Python supports asynchronous programming through asyncio [S1]({source.url}).",
        claims=[
            Claim(
                text="Python supports asynchronous programming through asyncio",
                source_id="S1",
                quote=PASSAGE,
            )
        ],
        verified=True,
    )
    return report, source


def test_audit_maps_claim_to_captured_passage_and_citation_without_claiming_entailment():
    report, source = sample()
    source.text = source.text.replace(" ", "\n")
    audit = audit_evidence(report, [source])
    assert audit["structural_checks_passed"] and audit["structural_match_rate"] == 1
    assert audit["claims"][0]["extracted_passage"] == PASSAGE.lower()
    assert audit["factual_acceptance"] == "not_established_by_structural_audit"


def test_url_without_fetched_support_cannot_pass():
    report, source = sample()
    source.text = "This unrelated page does not contain the submitted supporting passage."
    audit = audit_evidence(report, [source])
    assert not audit["structural_checks_passed"] and audit["claims"][0]["extracted_passage"] is None


def test_misdirected_rendered_citation_and_unknown_id_cannot_pass():
    report, source = sample()
    report.answer = (
        "A claim [S1](https://unrelated.example/) and [S99](https://unrelated.example/)."
    )
    audit = audit_evidence(report, [source])
    assert not audit["structural_checks_passed"] and audit["unknown_citations"] == ["S99"]


def test_duplicate_source_ids_are_ambiguous_and_fail():
    report, source = sample()
    assert not audit_evidence(report, [source, source])["structural_checks_passed"]


def test_quote_presence_does_not_establish_semantic_support_or_full_answer_coverage():
    report, source = sample()
    report.claims[0].text = "Python asyncio cures every disease."
    report.answer += "\n\nAnother unsupported factual statement."
    audit = audit_evidence(report, [source])
    assert audit["structural_checks_passed"]
    assert audit["claims"][0]["entailment"] == "requires_independent_review"
    assert audit["answer_claim_coverage"] == "requires_independent_review"
    assert len(audit["answer_review_segments"]) == 2


def test_historical_failed_runs_and_stable_chat_do_not_vacuously_pass_research_audit():
    result = audit_artifact(
        {
            "cases": [
                {"case": "blocked", "status": "failed", "report": {}, "retrieved_sources": []},
                {
                    "case": "normal",
                    "report": Report(answer="Hello!").model_dump(),
                    "retrieved_sources": [],
                },
            ]
        }
    )
    assert result["network_calls"] == 0
    assert [case["audit"]["status"] for case in result["cases"]] == [
        "no_generated_report",
        "no_evidence_claims",
    ]
    assert not any(case["audit"]["structural_checks_passed"] for case in result["cases"])


def test_report_embedded_sources_cannot_substitute_for_captured_extraction():
    report, source = sample()
    report.sources = [source]
    result = audit_artifact(
        {"cases": [{"case": "web", "report": report.model_dump(), "retrieved_sources": []}]}
    )
    assert not result["cases"][0]["audit"]["structural_checks_passed"]


def test_invalid_claim_schema_is_reported_without_a_false_pass():
    result = audit_artifact(
        {
            "cases": [
                {
                    "case": "web",
                    "report": {"answer": "A claim", "claims": [{"text": "Missing quote"}]},
                }
            ]
        }
    )
    assert result["cases"][0]["audit"]["status"] == "invalid_report_or_source_schema"
