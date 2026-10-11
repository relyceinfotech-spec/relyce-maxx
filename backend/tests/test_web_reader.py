import socket
import time

import httpx
import pytest
from conftest import context
from langgraph.checkpoint.memory import InMemorySaver
from test_research_graph import PASSAGE, Fetch, ResearchLLM, Search

from app.agents.chat.graph import build_graph
from app.core.errors import BudgetExceeded, EvidenceError, FetchRejected, LeaseLost
from app.domain.models import ChatReport, ChatVerification, Page, PassageClaim, Source, Verification
from app.research.audit import audit_evidence
from app.research.selection import prioritized_candidates
from app.research.verification import render_citations, structured_citations, validate_claims
from app.tools.fetcher import Fetcher
from app.tools.reader import PageReader, index_passages


class CountingFetch(Fetch):
    def __init__(self):
        self.calls = []

    async def fetch(self, url):
        self.calls.append(url)
        return await super().fetch(url)


async def test_cached_navigation_is_exact_bounded_and_no_refetch(settings):
    fetch = CountingFetch()
    ctx = context(settings, ResearchLLM(), search=Search(), fetcher=fetch)
    reader = PageReader(ctx)
    page = await reader.open_page("https://example.org/release#section", title="Release")
    assert page.source_id == "S1" and page.canonical_url == "https://example.org/release"
    matches = reader.find_in_page("S1", "signed artifacts")
    assert matches
    for passage in reader.read_more("S1", matches[0].passage_id):
        assert passage.text == page.text[passage.start_offset : passage.end_offset]
        assert len(passage.text) <= 700
    assert len(reader.read_more("S1", matches[0].passage_id)) <= 3
    assert not reader.find_in_page("S1", "xylophone impossiblephrase")
    assert await reader.open_page("S1") == page
    restored = PageReader(
        ctx, **{"pages": reader.snapshot()["reader_pages"], "aliases": reader.aliases}
    )
    assert await restored.open_page(page.url) == page
    assert len(fetch.calls) == ctx.budget.pages == 1
    with pytest.raises(FetchRejected):
        reader.read_more("S1", "S1-P999")
    with pytest.raises(KeyError):
        reader.find_in_page("S99", "signed")


async def test_redirect_alias_reuses_final_source(settings):
    class Redirect(CountingFetch):
        async def fetch(self, url):
            self.calls.append(url)
            return await Fetch().fetch("https://example.org/final")

    fetch = Redirect()
    ctx = context(settings, ResearchLLM(), fetcher=fetch)
    reader = PageReader(ctx)
    page = await reader.open_page("https://example.org/redirect")
    assert await reader.open_page("https://example.org/final") == page
    assert await reader.open_page("https://example.org/redirect") == page
    assert len(fetch.calls) == 1


def test_offsets_are_stable_for_unicode_long_sentences_and_repeated_text():
    text = "தமிழ் " + "Long text " * 160 + ".\n" + PASSAGE + "\n" + PASSAGE
    indexed = index_passages("S1", text)
    assert indexed == index_passages("S1", text)
    assert len({p.passage_id for p in indexed}) == len(indexed)
    assert all(text[p.start_offset : p.end_offset] == p.text for p in indexed)
    assert all(len(p.text) <= 700 for p in indexed)


async def test_search_metadata_is_not_extracted_evidence(settings):
    class Metadata(Search):
        async def search(self, query):
            return [
                {
                    "url": "https://example.org/a#b",
                    "title": "Title",
                    "snippet": PASSAGE,
                    "published_at": "19 hours ago",
                }
            ]

    ctx = context(settings, ResearchLLM(), search=Metadata(), fetcher=Fetch())
    reader = PageReader(ctx)
    candidate = (await reader.web_search("release"))[0]
    assert candidate["kind"] == "search_candidate" and candidate["snippet"] == PASSAGE
    page = await reader.open_page(candidate["url"], search_date=candidate["search_date"])
    assert page.search_date == "19 hours ago" and page.published_at is None
    assert page.date_confidence == "unknown"


async def test_unsupported_content_is_not_cached(settings):
    class Binary(Fetch):
        async def fetch(self, url):
            return url, 200, {"content-type": "application/octet-stream"}, b"x" * 1000

    reader = PageReader(context(settings, ResearchLLM(), fetcher=Binary()))
    with pytest.raises(FetchRejected):
        await reader.open_page("https://example.org/binary")
    assert not reader.pages


async def test_extraction_timeout_is_bounded_and_not_cached(settings, monkeypatch):
    import app.tools.reader as module

    async def timeout(*args):
        raise TimeoutError

    monkeypatch.setattr(module, "extract_async", timeout)
    reader = PageReader(context(settings, ResearchLLM(), fetcher=Fetch()))
    with pytest.raises(TimeoutError):
        await reader.open_page("https://example.org/timeout")
    assert not reader.pages


def test_page_cache_rejects_forged_offsets():
    with pytest.raises(ValueError):
        Page(
            source_id="S1",
            url="https://example.org/",
            canonical_url="https://example.org/",
            title="Title",
            text=PASSAGE,
            content_type="text/plain",
            status=200,
            passages=[
                {
                    "source_id": "S1",
                    "passage_id": "S1-P1",
                    "text": PASSAGE,
                    "start_offset": 1,
                    "end_offset": len(PASSAGE),
                }
            ],
        )


@pytest.mark.parametrize(
    "change", ["case", "offset", "url", "duplicate_source", "duplicate_passage"]
)
def test_structural_integrity_rejects_tampered_evidence(change):
    source = Source(
        id="S1",
        url="https://example.org/",
        canonical_url="https://example.org/",
        title="Title",
        text=PASSAGE,
    )
    claim = PassageClaim(
        text="Artifacts are signed", source_id="S1", passage_id="S1-P1", quote=PASSAGE
    )
    passages = [index_passages("S1", PASSAGE)[0].model_dump()]
    sources = [source]
    if change == "case":
        claim.quote = PASSAGE.lower()
    elif change == "offset":
        passages[0]["start_offset"] = 1
    elif change == "url":
        source.canonical_url = "https://evil.example/"
    elif change == "duplicate_source":
        sources.append(source)
    else:
        passages.append(passages[0])
    with pytest.raises(EvidenceError):
        validate_claims([claim], sources, passages)


def test_server_citations_group_claims_and_ignore_model_links():
    source = Source(id="S1", url="https://example.org/", title="Captured title", text=PASSAGE)
    claim = PassageClaim(
        text="Artifacts are signed", source_id="S1", passage_id="S1-P1", quote=PASSAGE
    )
    result = structured_citations([claim, claim], [source])
    assert result[0].claim_indices == [0, 1]
    assert result[0].url == source.url and result[0].title == source.title
    other = claim.model_copy(update={"source_id": "S2", "passage_id": "S2-P1"})
    with pytest.raises(EvidenceError):
        render_citations("Signed [S1].", [claim, other], [source], all_claims_cited=True)


async def test_adaptive_reading_skips_failed_candidate_and_duplicate(settings):
    class Candidates(Search):
        async def search(self, query):
            self.calls += 1
            return [
                {"url": "https://example.org/bad", "title": "Release"},
                {"url": "https://example.org/good", "title": "Release"},
                {"url": "https://example.org/good#fragment", "title": "Release"},
            ]

    class Pages(CountingFetch):
        async def fetch(self, url):
            if url.endswith("bad"):
                self.calls.append(url)
                raise FetchRejected("Unavailable")
            return await super().fetch(url)

    fetch = Pages()
    ctx = context(settings, ResearchLLM(), search=Candidates(), fetcher=fetch)
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert result["report"]["verified"]
    assert result["report"]["citations"][0]["passage_id"] == "S1-P1"
    assert len(fetch.calls) == 2 and len(result["reader_pages"]) == 1
    assert result["limitations"]


async def test_successful_page_cap_is_separate_from_attempt_cap(settings):
    settings.chat_web_success_pages = 2

    class Candidates(Search):
        async def search(self, query):
            return [{"url": f"https://example.org/{i}", "title": "Release"} for i in range(10)]

    fetch = CountingFetch()
    ctx = context(settings, ResearchLLM(), search=Candidates(), fetcher=fetch)
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert len(result["reader_pages"]) == ctx.budget.pages == 2


async def test_direct_url_reads_without_search(settings):
    search = Search()
    ctx = context(settings, ResearchLLM(), search=search, fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Summarize https://example.org/release"})
    assert result["report"]["verified"] and search.calls == 0


async def test_checkpoint_restart_reuses_completed_search_and_read(settings):
    saver, search, fetch = InMemorySaver(), Search(), CountingFetch()
    ctx = context(settings, ResearchLLM(), search=search, fetcher=fetch)
    config = {"configurable": {"thread_id": "owner:reader:1"}}
    graph = build_graph(ctx, saver)
    await graph.ainvoke(
        {"prompt": "Search the latest release"}, config, interrupt_after=["read_candidate"]
    )
    saved = await graph.aget_state(config)
    resumed = context(settings, ResearchLLM(), search=search, fetcher=fetch)
    resumed.job = ctx.job
    for key, value in saved.values["budget"].items():
        setattr(resumed.budget, key, value)
    result = await build_graph(resumed, saver).ainvoke(None, config)
    assert result["report"]["verified"]
    assert search.calls == len(fetch.calls) == resumed.budget.pages == 1


async def test_expired_checkpoint_deadline_prevents_provider_calls(settings):
    saver, search = InMemorySaver(), Search()
    ctx = context(settings, ResearchLLM(), search=search, fetcher=Fetch())
    config = {"configurable": {"thread_id": "owner:expired:1"}}
    graph = build_graph(ctx, saver)
    await graph.ainvoke(
        {"prompt": "Search the latest release"}, config, interrupt_after=["prepare_collection"]
    )
    await graph.aupdate_state(config, {"deadline_utc": time.time() - 1})
    with pytest.raises(BudgetExceeded):
        await graph.ainvoke(None, config)
    assert search.calls == 0


async def test_cancelled_lease_prevents_page_request(settings):
    fetch = CountingFetch()
    ctx = context(settings, ResearchLLM(), fetcher=fetch)

    async def cancelled(*args):
        raise LeaseLost("Cancelled")

    ctx.event = cancelled
    with pytest.raises(LeaseLost):
        await PageReader(ctx).open_page("https://example.org/")
    assert not fetch.calls


@pytest.mark.parametrize(
    "issue", ["negation", "numbers", "date", "scope", "attribution", "qualifier", "uncited_fact"]
)
async def test_semantic_and_coverage_rejections_withhold_even_valid_quotes(settings, issue):
    class Rejected(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema in (Verification, ChatVerification):
                assert "EVERY answer sentence" in messages[0]["content"]
                return schema(
                    supported=[True],
                    contradictions=[issue],
                    **(
                        {"answer_coverage_supported": True, "coverage_failures": []}
                        if schema is ChatVerification
                        else {}
                    ),
                )
            return await super().complete(messages, tier, schema)

    ctx = context(settings, Rejected(), search=Search(), fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"] and not result["report"]["citations"]
    assert not result["report"]["claims"]
    assert result["verification_result"]["contradiction_count"] == 1


async def test_dns_rebinding_between_robots_and_page_is_blocked(settings, monkeypatch):
    lookups = []

    def dns(*args, **kwargs):
        ip = "8.8.8.8" if not lookups else "127.0.0.1"
        lookups.append(ip)
        return [(2, 1, 6, "", (ip, 443))]

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(404)), **kw),
    )
    with pytest.raises(FetchRejected):
        await Fetcher(settings).fetch("https://example.org/article")
    assert lookups == ["8.8.8.8", "127.0.0.1"]


def test_candidate_ranking_keeps_new_sources_and_removes_fragment_duplicates():
    ordered = prioritized_candidates(
        [
            {"url": "https://unknown-new-source.org/a", "title": "Bananas"},
            {"url": "https://new-source.org/b", "title": "Release signed artifacts"},
            {"url": "https://new-source.org/b#x", "title": "Duplicate"},
        ],
        "Search release signed artifacts",
    )
    assert len(ordered) == 2 and ordered[0]["url"] == "https://new-source.org/b"


async def test_uncited_fact_fails_separate_coverage_gate(settings):
    class MissingCoverage(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatVerification:
                return ChatVerification(
                    supported=[True],
                    contradictions=[],
                    answer_coverage_supported=False,
                    coverage_failures=["An answer fact is absent from claims"],
                )
            return await super().complete(messages, tier, schema)

    ctx = context(settings, MissingCoverage(), search=Search(), fetcher=Fetch())
    diagnostics = []
    ctx.evidence_diagnostics = diagnostics.append
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert not result["report"]["verified"] and not result["report"]["citations"]
    assert not result["verification_result"]["answer_coverage_supported"]
    assert diagnostics[-1]["stage"] == "answer_coverage"


async def test_search_refinement_never_exceeds_iteration_cap(settings):
    class Empty(Search):
        async def search(self, query):
            self.calls += 1
            return []

    search = Empty()
    ctx = context(settings, ResearchLLM(), search=search, fetcher=Fetch())
    result = await build_graph(ctx).ainvoke({"prompt": "Search the latest release"})
    assert search.calls == settings.chat_web_search_iterations == 2
    assert not result["report"]["verified"] and not ctx.llm.calls


async def test_multiple_sources_have_separate_server_citation_targets(settings):
    class Multiple(Search):
        async def search(self, query):
            return [{"url": f"https://example.org/{i}", "title": f"Release {i}"} for i in range(2)]

    class Cited(ResearchLLM):
        async def complete(self, messages, tier="research", schema=None):
            if schema is ChatReport:
                return ChatReport(
                    answer="Artifacts are signed [S1]. Build metadata is provided [S2].",
                    sentence_claims=[
                        {"sentence_index": 0, "claim_indices": [0]},
                        {"sentence_index": 1, "claim_indices": [1]},
                    ],
                    claims=[
                        PassageClaim(
                            text="Signed artifacts",
                            source_id="S1",
                            passage_id="S1-P1",
                            quote=PASSAGE,
                        ),
                        PassageClaim(
                            text="Build metadata", source_id="S2", passage_id="S2-P1", quote=PASSAGE
                        ),
                    ],
                )
            if schema is ChatVerification:
                return ChatVerification(
                    supported=[True, True],
                    contradictions=[],
                    answer_coverage_supported=True,
                    coverage_failures=[],
                )
            return await super().complete(messages, tier, schema)

    result = await build_graph(
        context(settings, Cited(), search=Multiple(), fetcher=Fetch())
    ).ainvoke({"prompt": "Search the latest release"})
    assert result["report"]["verified"]
    assert [c["claim_indices"] for c in result["report"]["citations"]] == [[0], [1]]
    assert [c["url"] for c in result["report"]["citations"]] == [
        "https://example.org/0",
        "https://example.org/1",
    ]
    report = ChatReport.model_validate(result["report"])
    sources = [Source.model_validate(s) for s in result["sources"]]
    audit = audit_evidence(report, sources, result["evidence_passages"])
    assert audit["structural_checks_passed"]
    report.citations[0].url = "https://fabricated.example/"
    assert not audit_evidence(report, sources, result["evidence_passages"])[
        "structural_checks_passed"
    ]
