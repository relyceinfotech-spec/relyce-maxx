from datetime import date

import pytest
from conftest import context
from test_research_graph import PASSAGE, Fetch, ResearchLLM, Search

from app.agents.chat.graph import build_graph
from app.core.errors import EvidenceRejection
from app.domain.models import PassageClaim, Source
from app.research.evidence import numbered_passages, verification_payload
from app.research.selection import document_date, freshness_rejection, prioritized_candidates
from app.research.verification import validate_claims


@pytest.mark.parametrize(
    "source_id,passage_id,quote,reason",
    [
        ("S99", "S1-P1", PASSAGE, "unknown_source_id"),
        ("S1", "S1-P1", "An invented quote not in evidence", "quote_not_found"),
        ("S1", "S1-P99", PASSAGE, "unknown_passage_id"),
        ("S1", "S2-P1", PASSAGE, "passage_source_mismatch"),
    ],
)
def test_strict_rejections_identify_claim_without_raw_quote(source_id, passage_id, quote, reason):
    source = Source(id="S1", url="https://example.org/", title="Source", text=PASSAGE)
    passages = [
        {"source_id": "S1", "passage_id": "S1-P1", "text": PASSAGE},
        {"source_id": "S2", "passage_id": "S2-P1", "text": PASSAGE},
    ]
    claim = PassageClaim(text="A claim", source_id=source_id, passage_id=passage_id, quote=quote)
    with pytest.raises(EvidenceRejection) as caught:
        validate_claims([claim], [source], passages)
    failure = caught.value.failures[0]
    assert failure["claim_index"] == 0 and failure["reason"] == reason
    assert failure["source_id"] == source_id and failure["quote_length"] == len(quote)
    assert quote not in str(failure)


def test_numbered_passage_quotes_remain_in_original_source():
    source = Source(
        id="S1", url="https://example.org/", title="Source", text=PASSAGE + "\n" + PASSAGE
    )
    evidence, passages = numbered_passages([source.model_dump()], "signed artifacts")
    assert evidence[0]["passages"] == passages
    claim = PassageClaim(
        text="Artifacts are signed",
        source_id="S1",
        passage_id=passages[0]["passage_id"],
        quote=passages[0]["text"],
    )
    validate_claims([claim], [source], passages)


def test_whitespace_matching_entailment_receives_actual_captured_text():
    text = PASSAGE.replace(" ", "\n")
    source = Source(
        id="S1", url="https://example.org/", title="Source", text=text, published_at="2026-10-09"
    )
    claim = PassageClaim(
        text="Artifacts are signed", source_id="S1", passage_id="S1-P1", quote=PASSAGE
    )
    validate_claims([claim], [source])
    payload = verification_payload("Signed [S1]", [claim], [source])
    assert text in payload["evidence"][0]["passages"][0]
    assert payload["evidence"][0]["published_at"] == "2026-10-09"


def test_official_announcements_precede_coverage_and_social_pages():
    urls = [
        "https://www.facebook.com/story",
        "https://docs.python.org/3/whatsnew/3.12.html",
        "https://www.infoworld.com/python",
        "https://blog.python.org/2026/release",
        "https://www.reddit.com/story",
    ]
    ordered = prioritized_candidates([{"url": url} for url in urls], "Python this week")
    assert ordered[0]["url"] == urls[3] and ordered[1]["url"] == urls[2]
    assert {r["url"] for r in ordered[-2:]} == {urls[0], urls[4]}


def test_fresh_crawl_label_does_not_make_2023_release_notes_current():
    assert (
        freshness_rejection(
            "https://docs.python.org/3/whatsnew/3.12.html",
            "Python 3.12 was released on October 2, 2023.",
            None,
            "Python this week",
            date(2026, 10, 11),
        )
        == "historical_release_notes"
    )


def test_document_published_metadata_excludes_modified_dates():
    assert (
        document_date(b'<meta property="article:modified_time" content="2026-10-11">', "An article")
        is None
    )
    assert document_date(
        b'<script>{"datePublished":"2026-10-08T12:00:00Z"}</script>', "An article"
    ) == date(2026, 10, 8)
    assert (
        freshness_rejection(
            "https://example.org/",
            "An article",
            date(2023, 1, 1),
            "Python this week",
            date(2026, 10, 11),
        )
        == "document_publication_outside_requested_week"
    )


async def test_local_rejection_callback_identifies_unknown_source_and_public_answer_is_withheld(
    settings,
):
    entries = []
    ctx = context(settings, ResearchLLM(fabricated=True), search=Search(), fetcher=Fetch())
    ctx.evidence_diagnostics = entries.append
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["claims"] and not result["report"]["verified"]
    assert entries[0]["failures"][0]["reason"] == "unknown_source_id"
    assert entries[0]["failures"][0]["claim_index"] == 0
    assert PASSAGE not in str(entries)
