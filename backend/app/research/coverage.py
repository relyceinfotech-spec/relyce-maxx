"""Sentence references are structural mappings, not proof of semantic entailment."""

import re

from app.core.errors import EvidenceError


class CoverageRejection(EvidenceError):
    def __init__(self, category, sentence_indices):
        super().__init__("Answer sentence mapping failed")
        self.sentence_issues = [
            {
                "category": category,
                "sentence_indices": sentence_indices,
                "claim_indices": [],
                "passage_ids": [],
            }
        ]


def answer_sentences(answer):
    # Decimal/version dots do not split; paragraphs/bullets are independent units.
    pieces = re.split(r"\n+|(?<=[.!?])\s+(?!\[[SKP]\d+\])(?=\S)", answer.strip())
    return [
        {"sentence_index": i, "text": text}
        for i, text in enumerate(part.strip() for part in pieces if part.strip())
    ]


def validate_sentence_claims(report, *, required=False, check_citations=True):
    units = answer_sentences(report.answer)
    mappings = report.sentence_claims
    # Older checkpoints did not have mappings. Independent full-answer verification
    # remains mandatory for them; new synthesis explicitly requires mappings.
    if not mappings:
        if required:
            raise CoverageRejection("missing_claim", [u["sentence_index"] for u in units][:80])
        return units
    by_index = {m.sentence_index: m for m in mappings}
    if len(by_index) != len(mappings) or set(by_index) != set(range(len(units))):
        raise CoverageRejection(
            "missing_claim", sorted(set(range(len(units))) - set(by_index))[:80]
        )
    for unit in units:
        indices = by_index[unit["sentence_index"]].claim_indices
        if len(set(indices)) != len(indices) or any(
            i < 0 or i >= len(report.claims) for i in indices
        ):
            raise CoverageRejection("missing_claim", [unit["sentence_index"]])
        cited = set(re.findall(r"\[([SKP]\d+)\]", unit["text"]))
        mapped = {report.claims[i].source_id for i in indices}
        if check_citations and (not cited or cited != mapped):
            raise CoverageRejection("missing_citation", [unit["sentence_index"]])
    return units


def attach_sentence_citations(report):
    """Resolve markers from validated claim indexes, before independent verification."""
    units = validate_sentence_claims(report, required=True, check_citations=False)
    if re.search(r"https?://|\]\(", report.answer, re.I):
        raise EvidenceError("Model-generated links are forbidden")
    known = {c.source_id for c in report.claims}
    typed = set(re.findall(r"\[([SKP]\d+)\]", report.answer))
    if not typed <= known:
        raise EvidenceError("Unknown model citation identifier")
    by_index = {m.sentence_index: m for m in report.sentence_claims}
    lines = []
    for unit in units:
        text = re.sub(r"\s*\[[SKP]\d+\]", "", unit["text"]).strip()
        text = re.sub(r"[.!?]\s*[.!?]+$", ".", text)
        ending = text[-1] if text and text[-1] in ".!?" else ""
        body = text[:-1].rstrip() if ending else text
        ids = list(
            dict.fromkeys(
                report.claims[i].source_id for i in by_index[unit["sentence_index"]].claim_indices
            )
        )
        lines.append(body + " " + " ".join(f"[{sid}]" for sid in ids) + ending)
    report.answer = "\n".join(lines)
    validate_sentence_claims(report, required=True)


def indexed_verification_payload(report, payload):
    return {
        **payload,
        "answer_sentences": answer_sentences(report.answer),
        "sentence_claims": [m.model_dump() for m in report.sentence_claims],
    }


def validate_issue_references(verdict, report, passages):
    unit_count = len(answer_sentences(report.answer))
    known = {p["passage_id"] for p in passages}
    for issue in verdict.issues:
        if (
            any(i < 0 or i >= unit_count for i in issue.sentence_indices)
            or any(i < 0 or i >= len(report.claims) for i in issue.claim_indices)
            or not set(issue.passage_ids) <= known
        ):
            raise EvidenceError("Verifier issue contains unknown references")


def issue_diagnostics(verdict):
    # No verifier prose, draft sentences, quotes or prompts in development records.
    return [issue.model_dump() for issue in verdict.issues]
