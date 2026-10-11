from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.discovery.engine import discovery_url, rss_items
from app.services.feed import rank


def post(topic="ai", interest=1, age=0, **extra):
    return {
        "id": uuid4(),
        "primary_topic": topic,
        "interest": interest,
        "published_at": datetime.now(UTC) - timedelta(hours=age),
        "hidden": False,
        "blocked": False,
        **extra,
    }


def test_feed_removes_hidden_and_blocked_posts():
    visible = post()
    result = rank([visible, post(hidden=True), post(blocked=True)], "for_you", 10)
    assert [p["id"] for p in result] == [visible["id"]]


def test_following_excludes_unfollowed_posts():
    assert len(rank([post(interest=0), post()], "following", 10)) == 1


def test_latest_preserves_chronology():
    fresh, old = post(interest=0), post(age=10)
    assert rank([old, fresh], "latest", 10)[0]["id"] == fresh["id"]


def test_diversity_avoids_three_consecutive_topics():
    result = rank([post(), post(), post(), post(topic="programming", interest=0.2)], "for_you", 4)
    assert result[2]["primary_topic"] == "programming"


def test_canonical_discovery_preserves_meaningful_query():
    assert (
        discovery_url("https://EXAMPLE.com/article?id=42&utm_source=feed#anchor")
        == "https://example.com/article?id=42"
    )


def test_rss_normalizes_real_entries():
    data = b'<rss version="2.0"><channel><title>Source</title><item><title>Release</title><link>https://example.com/release</link><guid>release-1</guid><pubDate>Sat, 10 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'
    items = rss_items(data)
    assert items[0]["external_id"] == "release-1"
    assert items[0]["published_at"].tzinfo == UTC
