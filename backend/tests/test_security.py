import socket

import httpx
import pytest

from app.core.errors import EvidenceError, FetchRejected
from app.domain.models import Claim, Source
from app.research.verification import render_citations, validate_claims
from app.tools.fetcher import Fetcher, canonical_url, public_addresses


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:pass@example.com",
        "https://example.com:8000",
        "ftp://example.com",
    ],
)
def test_forbidden_url_forms(url):
    with pytest.raises(FetchRejected):
        canonical_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"])
async def test_internal_ips_are_blocked(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (ip, 443))])
    with pytest.raises(FetchRejected):
        await public_addresses("evil.test", 443)


async def test_mixed_dns_is_blocked(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(FetchRejected):
        await public_addresses("evil.test", 443)


async def test_connection_is_pinned_to_public_ip(monkeypatch, settings):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
    observed = []
    original = httpx.AsyncClient

    def handle(request):
        observed.append(request)
        return httpx.Response(200, content=b"data")

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    _, _, data = await Fetcher(settings)._request("https://example.com/article")
    assert data == b"data"
    assert observed[0].url.host == "8.8.8.8"
    assert observed[0].headers["host"] == "example.com"
    # httpcore/anyio expects a hostname string for TLS wrapping, not pre-encoded bytes.
    assert observed[0].extensions["sni_hostname"] == "example.com"


async def test_size_limit(monkeypatch, settings):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 10001)), **kw
        ),
    )
    with pytest.raises(FetchRejected):
        await Fetcher(settings)._request("https://example.com", max_bytes=10000)


def test_claim_requires_exact_quote():
    source = Source(
        id="S1", url="https://example.com", title="Source", text="The documented quote exists here."
    )
    claim = Claim(
        text="Unsupported", source_id="S1", quote="A totally fabricated supporting quotation."
    )
    with pytest.raises(EvidenceError):
        validate_claims([claim], [source])


@pytest.mark.parametrize(
    "answer", ["Claim [S2].", "Claim https://evil.com [S1].", "Claim without a citation."]
)
def test_invalid_citations(answer):
    source = Source(
        id="S1", url="https://example.com", title="Source", text="The documented quote exists here."
    )
    claim = Claim(text="Documented quote", source_id="S1", quote=source.text)
    with pytest.raises(EvidenceError):
        render_citations(answer, [claim], [source])
