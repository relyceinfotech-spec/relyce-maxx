"""Collect 100 real RSS headlines locally, without models or a hosted database.

Feed topics are sampling hints, not ground-truth labels. Annotation must happen
before scoring; a publisher's topic does not establish an article's subject.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import feedparser

from app.core.config import Settings
from app.core.runtime import event_loop
from app.tools.fetcher import Fetcher

FEEDS = [
    ("ai", "https://huggingface.co/blog/feed.xml"),
    ("ai", "https://blog.google/technology/ai/rss/"),
    ("programming", "https://blog.python.org/feeds/posts/default?alt=rss"),
    ("programming", "https://github.blog/feed/"),
    ("cybersecurity", "https://www.bleepingcomputer.com/feed/"),
    ("cybersecurity", "https://feeds.feedburner.com/TheHackersNews"),
]
OUTPUT_DIRECTORY = Path(__file__).resolve().parents[1] / ".local-state"


async def main():
    fetcher = Fetcher(Settings(_env_file=None))
    candidates, errors = [], []
    for topic, url in FEEDS:
        try:
            final, _, _, body = await fetcher.fetch(url)
            parsed = feedparser.parse(body)
            for entry in parsed.entries[:100]:
                link = entry.get("link") or entry.get("id")
                if link and link.startswith("https://"):
                    candidates.append(
                        {
                            "title": entry.get("title", "")[:300],
                            "url": link,
                            "published_at": entry.get("published") or entry.get("updated"),
                            "feed_url": final,
                            "sampling_topic": topic,
                            "expected_topic": None,
                            "label_status": "needs_review",
                        }
                    )
        except Exception as exc:
            errors.append({"feed": url, "error": type(exc).__name__})
        print(f"RSS sampling: {topic}; candidates={len(candidates)}", flush=True)
    quotas, rows, seen = {"ai": 34, "programming": 33, "cybersecurity": 33}, [], set()
    for candidate in candidates:
        topic = candidate["sampling_topic"]
        if quotas[topic] and candidate["url"] not in seen:
            rows.append({"id": f"RSS{len(rows) + 1:03}", **candidate})
            seen.add(candidate["url"])
            quotas[topic] -= 1
    folder = OUTPUT_DIRECTORY
    folder.mkdir(exist_ok=True)
    output = folder / "discovery-eval-candidates.json"
    output.write_text(
        json.dumps(
            {
                "collected_at": datetime.now(UTC).isoformat(),
                "ground_truth_ready": False,
                "count": len(rows),
                "missing_by_topic": quotas,
                "errors": errors,
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Saved {len(rows)}/100 actual RSS items for annotation: {output}")
    if len(rows) != 100:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main(), loop_factory=event_loop)
