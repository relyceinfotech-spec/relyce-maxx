import hashlib
import re
from urllib.parse import urlsplit

from app.core.errors import EvidenceError, EvidenceRejection, FetchRejected
from app.domain.models import Citation, Claim, Source
from app.tools.fetcher import canonical_url


def normalize(text: str, *, case_sensitive=False) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized if case_sensitive else normalized.casefold()


def validate_claims(claims: list[Claim], sources: list[Source], passages=None):
    by_id = {source.id: source for source in sources}
    by_passage = {p["passage_id"]: p for p in passages or []}
    if len(by_id) != len(sources) or len(by_passage) != len(passages or []):
        raise EvidenceError("Duplicate source or passage identifiers")
    if not claims:
        raise EvidenceError("No supported claims")
    failures = []
    for index, claim in enumerate(claims):
        source = by_id.get(claim.source_id)
        passage_id = getattr(claim, "passage_id", None)
        passage = by_passage.get(passage_id)
        quote = normalize(claim.quote, case_sensitive=passages is not None)
        found = bool(
            source and quote in normalize(source.text, case_sensitive=passages is not None)
        )
        valid_url = bool(source and urlsplit(source.url).scheme in ("http", "https"))
        canonical_matches = True
        if passages is not None and source:
            try:
                resolved = canonical_url(source.url)
                canonical_matches = not source.canonical_url or resolved == source.canonical_url
            except (FetchRejected, ValueError):
                valid_url = False
        reason = None
        if not source:
            reason = "unknown_source_id"
        elif not found:
            reason = "quote_not_found"
        elif not valid_url:
            reason = "invalid_source_url"
        elif not canonical_matches:
            reason = "canonical_url_mismatch"
        elif passages is not None:
            if not passage:
                reason = "unknown_passage_id"
            elif passage["source_id"] != claim.source_id:
                reason = "passage_source_mismatch"
            elif quote not in normalize(passage["text"], case_sensitive=True):
                reason = "quote_not_in_selected_passage"
            elif (
                "start_offset" in passage
                and source.text[passage["start_offset"] : passage["end_offset"]] != passage["text"]
            ):
                reason = "passage_offset_mismatch"
        if reason:
            failures.append(
                {
                    "claim_index": index,
                    "source_id": claim.source_id,
                    "passage_id": passage_id,
                    "reason": reason,
                    "quote_length": len(claim.quote),
                    "passage_found": found,
                    "source_found": source is not None,
                    "passage_lookup_succeeded": passage is not None,
                    "normalized_match": found,
                    "source_url_valid": valid_url,
                    "canonical_url_match": canonical_matches,
                    "quote_sha256": hashlib.sha256(claim.quote.encode()).hexdigest(),
                }
            )
    if failures:
        raise EvidenceRejection(failures)


def render_citations(
    answer: str,
    claims: list[Claim],
    sources: list[Source],
    *,
    required: bool = True,
    all_claims_cited: bool = False,
) -> str:
    """Model emits [S1] identifiers. Only server-known URLs become links."""
    by_id = {s.id: s for s in sources}
    referenced = set(re.findall(r"\[((?:S|K|P)\d+)\]", answer))
    supported = {c.source_id for c in claims}
    if (
        (required and not referenced)
        or not referenced <= supported
        or not referenced <= by_id.keys()
        or (all_claims_cited and not supported <= referenced)
    ):
        raise EvidenceError("Missing, unsupported or fabricated citation identifiers")
    if re.search(r"https?://|\]\(", answer, re.I):
        raise EvidenceError("Model-generated links are forbidden; use source identifiers")
    for source_id in referenced:
        url = by_id[source_id].url.replace("(", "%28").replace(")", "%29")
        answer = answer.replace(f"[{source_id}]", f"[{source_id}]({url})")
    return answer


def structured_citations(claims, sources):
    """Ignore all model citation metadata; resolve only from validated server sources."""
    by_id = {s.id: s for s in sources}
    grouped = {}
    for index, claim in enumerate(claims):
        key = (claim.source_id, claim.passage_id)
        if key not in grouped:
            source = by_id[claim.source_id]
            grouped[key] = Citation(
                source_id=source.id,
                passage_id=claim.passage_id,
                url=source.canonical_url or source.url,
                title=source.title,
                claim_indices=[],
            )
        grouped[key].claim_indices.append(index)
    return list(grouped.values())
