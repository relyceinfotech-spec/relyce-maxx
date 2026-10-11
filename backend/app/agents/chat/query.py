"""Deterministic date anchoring for simple searches; deep planning stays in the graph."""

import re
from datetime import UTC, datetime, timedelta


def search_query(prompt, today=None):
    today = today or datetime.now(UTC).date()
    query = prompt.split("?", 1)[0].strip()
    if re.search(r"this week|\b(latest|recent|today|current|news)\b|what.s new", prompt, re.I):
        start = today - timedelta(days=today.weekday() if "this week" in prompt.lower() else 7)
        query += f" after:{start.isoformat()} before:{(today + timedelta(days=1)).isoformat()}"
    return query[:500]


def is_url_reading(prompt):
    remainder = re.sub(r"https?://[^\s<>]+", "", prompt).strip(" .!?")
    return bool(re.search(r"https?://", prompt)) and bool(
        re.fullmatch(
            r"(please )?(summarize|read|explain)( this| the page| the article)?", remainder, re.I
        )
    )
