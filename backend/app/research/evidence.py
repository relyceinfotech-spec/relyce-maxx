import re

from app.research.content import boilerplate_only
from app.retrieval.knowledge import chunks
from app.tools.reader import index_passages, relevant_passages


def excerpts(sources: list[dict], question: str, max_sources=8):
    words = set(re.findall(r"\w{4,}", question.casefold()))
    result = []
    for source in sources[:max_sources]:
        passages = chunks(source["text"], size=1600, overlap=200)
        best = max(
            passages,
            key=lambda p: len(words & set(re.findall(r"\w{4,}", p.casefold()))),
            default="",
        )
        result.append({**source, "text": best})
    return result


def numbered_passages(sources: list[dict], question: str, max_sources=8, selections=None):
    """Select short source-bound spans; IDs remain stable in the stored selection."""
    evidence, selected = [], []
    for source in sources[:max_sources]:
        if boilerplate_only(source["text"]):
            continue
        indexed = index_passages(source["id"], source["text"])
        ranked = (
            (selections or {}).get(source["id"])
            or relevant_passages(indexed, question)
            or indexed[:3]
        )
        compact = {}
        for passage in sorted(ranked, key=lambda p: p.start_offset):
            key = re.sub(r"\s+", " ", passage.text).strip()
            compact.setdefault(key, passage)
        passages = [p.model_dump() for p in compact.values()]
        if not passages:
            continue
        selected.extend(passages)
        evidence.append(
            {
                "id": source["id"],
                "url": source["url"],
                "title": source["title"],
                "published_at": source.get("published_at"),
                "date_confidence": "document" if source.get("published_at") else "unknown",
                "passages": passages,
            }
        )
    return evidence, selected


def verification_payload(answer, claims, sources, *, compact=False):
    # Exact quote presence is checked deterministically before entailment verification.
    # Include short surrounding context rather than repeating entire articles.
    relevant = []
    for source in sources:
        quotes = [c.quote for c in claims if c.source_id == source.id]
        if not quotes:
            continue
        contexts = []
        ranges = []
        for quote in quotes:
            pos = source.text.casefold().find(quote.casefold())
            if pos >= 0:
                contexts.append(source.text[max(0, pos - 200) : pos + len(quote) + 200])
                ranges.append((max(0, pos - 200), min(len(source.text), pos + len(quote) + 200)))
            else:
                # Whitespace-insensitive matching must still return captured evidence, never a submitted quote.
                pattern = r"\s+".join(re.escape(word) for word in quote.split())
                match = re.search(pattern, source.text, re.I) if pattern else None
                contexts.append(
                    source.text[max(0, match.start() - 200) : match.end() + 200] if match else ""
                )
                if match:
                    ranges.append(
                        (max(0, match.start() - 200), min(len(source.text), match.end() + 200))
                    )
        if compact:
            merged = []
            for start, end in sorted(ranges):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            contexts = [source.text[start:end] for start, end in merged]
        relevant.append(
            {
                "id": source.id,
                "url": source.url,
                "published_at": source.published_at,
                "passages": contexts,
            }
        )
    return {"answer": answer, "claims": [c.model_dump() for c in claims], "evidence": relevant}
