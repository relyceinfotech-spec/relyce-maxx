import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.core.errors import FetchRejected

USER_AGENT = "MAXXXResearchBot/0.1"


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise FetchRejected("Only public HTTP(S) URLs without credentials are allowed")
    if parts.port not in (None, 443 if parts.scheme == "https" else 80):
        raise FetchRejected("Nonstandard port is not allowed")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, "")
    )


async def public_addresses(host: str, port: int) -> list[str]:
    addresses = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    ips = sorted({item[4][0] for item in addresses})
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise FetchRejected("Private, loopback, reserved or mixed DNS destination blocked")
    return ips


class Fetcher:
    """Resolve and pin public IPs, revalidate every redirect, enforce robots and size limits."""

    def __init__(self, settings):
        self.settings = settings
        self.robots: dict[str, RobotFileParser] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def _request(self, url: str, headers=None, max_bytes=None):
        url = canonical_url(url)
        parts = urlsplit(url)
        host = parts.hostname
        ips = await public_addresses(host, parts.port or (443 if parts.scheme == "https" else 80))
        # Pin the URL to the resolved public address. Preserve Host and TLS SNI.
        # trust_env=False prevents a proxy from resolving the original host again.
        ip = ips[0]
        netloc = f"[{ip}]" if ":" in ip else ip
        pinned = urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds, trust_env=False, follow_redirects=False
        ) as client:
            async with client.stream(
                "GET",
                pinned,
                headers={"Host": host, "User-Agent": USER_AGENT, **(headers or {})},
                extensions={"sni_hostname": host},
            ) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308):
                    return status, response.headers, b""
                if status == 304:
                    return status, response.headers, b""
                response.raise_for_status()
                limit = max_bytes or self.settings.fetch_max_bytes
                if int(response.headers.get("content-length", 0)) > limit:
                    raise FetchRejected("Response too large")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise FetchRejected("Response too large")
                return status, response.headers, bytes(data)

    async def _allowed(self, url: str):
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            parser = RobotFileParser()
            try:
                status, _, data = await self._request(origin + "/robots.txt", max_bytes=256000)
                if status != 200:
                    raise FetchRejected("Robots redirect requires manual source configuration")
                parser.parse(data.decode("utf-8", errors="replace").splitlines())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise FetchRejected("Robots policy unavailable") from exc
                parser.parse([])
            self.robots[origin] = parser
        if not self.robots[origin].can_fetch(USER_AGENT, url):
            raise FetchRejected("Robots disallows this URL")
        return min(max(self.robots[origin].crawl_delay(USER_AGENT) or 1, 1), 30)

    async def fetch(self, url: str, headers=None):
        for _ in range(5):
            url = canonical_url(url)
            delay = await self._allowed(url)
            host = urlsplit(url).hostname
            lock = self.locks.setdefault(host, asyncio.Lock())
            async with lock:
                status, response_headers, data = await self._request(url, headers)
                await asyncio.sleep(delay)
            if status in (301, 302, 303, 307, 308):
                location = response_headers.get("location")
                if not location:
                    raise FetchRejected("Redirect missing destination")
                url = urljoin(url, location)
                headers = None
                continue
            return url, status, response_headers, data
        raise FetchRejected("Too many redirects")
