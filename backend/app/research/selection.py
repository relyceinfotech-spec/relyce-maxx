"""Candidate ordering and explicit document-date checks; search labels are not dates."""

import re
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.core.errors import FetchRejected
from app.tools.fetcher import canonical_url

SOCIAL = {"facebook.com", "reddit.com", "instagram.com", "x.com", "twitter.com", "tiktok.com"}


def prioritized_candidates(results, question):
    words = set(re.findall(r"\w{3,}", question.casefold())) - {
        "the",
        "what",
        "this",
        "week",
        "latest",
        "search",
        "web",
        "new",
    }

    def priority(result):
        url = urlsplit(result["url"])
        host = (url.hostname or "").removeprefix("www.")
        rank = 10
        if any(host == social or host.endswith("." + social) for social in SOCIAL):
            rank = 100
        if "python" in question.lower() and (host == "python.org" or host.endswith(".python.org")):
            rank = 0 if "/whatsnew/" not in url.path else 20
        relevance = len(
            words
            & set(
                re.findall(
                    r"\w{3,}",
                    (result.get("title", "") + " " + result.get("snippet", "")).casefold(),
                )
            )
        )
        # Search dates are only ranking hints, never verified document publication dates.
        date_hint = str(result.get("search_date") or result.get("published_at") or "")
        dated = bool(re.match(r"\d{4}-\d{2}-\d{2}", date_hint))
        return rank, -relevance, -int(dated)

    unique = {}
    for result in results:
        try:
            url = canonical_url(result["url"])
        except (FetchRejected, ValueError):
            continue
        unique.setdefault(url, {**result, "url": url})
    return sorted(unique.values(), key=priority)


class PublicationMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.dates = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "meta" and (attributes.get("property") or attributes.get("name", "")).lower() in {
            "article:published_time",
            "datepublished",
            "pubdate",
            "publication_date",
        }:
            self.dates.append(attributes.get("content", ""))


def document_date(data, text):
    html = data[:500000].decode("utf-8", errors="ignore")
    parser = PublicationMetadata()
    parser.feed(html)
    candidates = [*parser.dates, *re.findall(r'"datePublished"\s*:\s*"([^"\\]+)"', html)]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            return parsed.astimezone(UTC).date() if parsed.tzinfo else parsed.date()
        except ValueError:
            try:
                return date.fromisoformat(candidate[:10])
            except ValueError:
                continue
    # Explicit dates in the article opening; modified/search dates are deliberately excluded.
    match = re.search(
        r"(?:Published|Posted|Released|Release date)\s*(?:on|:)?\s*(\d{4}-\d{2}-\d{2})",
        text[:1200],
        re.I,
    )
    try:
        return date.fromisoformat(match[1]) if match else None
    except ValueError:
        return None


def freshness_rejection(url, text, published, question, today=None):
    today = today or datetime.now(UTC).date()
    if "this week" not in question.lower():
        return None
    start = today - timedelta(days=today.weekday())
    # A current crawl of historical version notes does not turn an old release into news.
    if "/whatsnew/" in urlsplit(url).path and re.search(
        r"Python [\d.]+ was released on", text, re.I
    ):
        match = re.search(r"was released on ([A-Za-z]+ \d{1,2}, \d{4})", text, re.I)
        if match:
            try:
                released = datetime.strptime(match[1], "%B %d, %Y").date()
                if not start <= released <= today:
                    return "historical_release_notes"
            except ValueError:
                pass
    if published and not start <= published <= today:
        return "document_publication_outside_requested_week"
    return None
