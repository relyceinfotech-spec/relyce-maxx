"""Offline claim-to-passage audit; structural matches do not establish entailment."""

import re
from collections import Counter
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.core.errors import EvidenceError
from app.domain.models import ChatReport, Passage, Report, Source
from app.research.verification import normalize, structured_citations, validate_claims


def audit_evidence(report: Report | None, extracted_sources: list[Source], passages=None) -> dict:
    if report is None:
        return {"status": "no_generated_report", "structural_checks_passed": False, "claims": []}
    if not report.claims:
        return {
            "status": "no_evidence_claims",
            "structural_checks_passed": False,
            "claims": [],
            "note": "Stable chat may not require citations; no research answer is accepted by this audit.",
        }
    duplicates = {
        sid for sid, count in Counter(s.id for s in extracted_sources).items() if count > 1
    }
    sources = {s.id: s for s in extracted_sources if s.id not in duplicates}
    passage_checks = None
    if isinstance(report, ChatReport):
        passage_checks = False
        try:
            if passages is not None:
                captured = [Passage.model_validate(p).model_dump() for p in passages]
                validate_claims(report.claims, extracted_sources, captured)
                expected = structured_citations(report.claims, extracted_sources)
                passage_checks = report.citations == expected
        except (EvidenceError, ValidationError):
            pass
    citations = re.findall(r"\[((?:S|K|P)\d+)\](?:\(([^\n)]*)\))?", report.answer)
    rows = []
    for index, claim in enumerate(report.claims):
        source = sources.get(claim.source_id)
        text = normalize(source.text) if source else ""
        quote = normalize(claim.quote)
        offset = text.find(quote) if quote else -1
        expected_url = source.url.replace("(", "%28").replace(")", "%29") if source else None
        targets = [target for sid, target in citations if sid == claim.source_id]
        checks = {
            "extracted_source_present": source is not None,
            "quote_present_in_extracted_text": offset >= 0,
            "web_url_scheme_valid": bool(
                source and urlsplit(source.url).scheme in ("http", "https")
            ),
            "rendered_citation_matches_source": bool(targets)
            and all(target == expected_url for target in targets),
        }
        if passage_checks is not None:
            checks["passage_registry_and_structured_citations_valid"] = passage_checks
        rows.append(
            {
                "claim_index": index,
                "claim": claim.text,
                "source_id": claim.source_id,
                "passage_id": getattr(claim, "passage_id", None),
                "source_url": source.url if source else None,
                "submitted_quote": claim.quote,
                "extracted_passage": text[max(0, offset - 160) : offset + len(quote) + 160]
                if offset >= 0
                else None,
                "passage_format": "casefolded_whitespace_normalized",
                "checks": checks,
                "passage_and_citation_match": all(checks.values()),
                "entailment": "requires_independent_review",
            }
        )
    declared_ids = {c.source_id for c in report.claims}
    unknown_citations = sorted(
        {sid for sid, _ in citations if sid not in declared_ids or sid not in sources}
    )
    matched = sum(row["passage_and_citation_match"] for row in rows)
    passed = matched == len(rows) and not duplicates and not unknown_citations
    # Review every answer paragraph: claims omitted by generation cannot be assumed covered.
    return {
        "status": "structural_pass" if passed else "structural_failure",
        "structural_checks_passed": passed,
        "claim_count": len(rows),
        "matched_claim_count": matched,
        "structural_match_rate": matched / len(rows),
        "claims": rows,
        "duplicate_source_ids": sorted(duplicates),
        "passage_and_structured_citation_checks": passage_checks,
        "unknown_citations": unknown_citations,
        "upstream_report_verified": report.verified,
        "answer_review_segments": [
            part.strip() for part in re.split(r"\n\s*\n", report.answer) if part.strip()
        ],
        "answer_claim_coverage": "requires_independent_review",
        "factual_acceptance": "not_established_by_structural_audit",
        "live_link_availability": "not_checked",
    }
