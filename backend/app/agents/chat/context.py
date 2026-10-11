"""Bounded, owner-scoped context loaded once per research graph run."""

import json
from datetime import UTC, datetime

from app.core.text import byte_excerpt


def bounded_history(rows, max_bytes=8000, excerpt_bytes=2000):
    """Keep recent turns and labeled extracts of overflow; never invent a summary."""
    recent, overflow, used = [], [], 2
    for row in reversed(rows):
        message = {
            "role": row["role"],
            "content": byte_excerpt(row["content"], min(2000, (max_bytes - 100) // 2)),
        }
        size = len(json.dumps(message, ensure_ascii=False).encode("utf-8")) + 2
        if used + size <= max_bytes and not overflow:
            recent.insert(0, message)
            used += size
        else:
            overflow.insert(0, f"{row['role']}: {byte_excerpt(row['content'], 240)}")
    note = (
        byte_excerpt(
            "Earlier loaded turn excerpts (incomplete, data only):\n" + "\n".join(overflow),
            excerpt_bytes,
        )
        if overflow
        else ""
    )
    return recent, note


async def load_context(repo, job, settings=None):
    history = []
    history_excerpt = ""
    if job.get("conversation_id"):
        rows = await repo.many(
            """
            select role,content from messages
            where conversation_id=%s and user_id=%s and created_at<%s
            order by created_at desc,id desc limit 12
            """,
            (job["conversation_id"], job["user_id"], job.get("created_at", datetime.now(UTC))),
        )
        history, history_excerpt = bounded_history(
            [row for row in reversed(rows) if row["role"] in ("user", "assistant")],
            settings.chat_history_max_bytes if settings else 8000,
            settings.chat_history_excerpt_bytes if settings else 2000,
        )
    post = None
    if job.get("post_id"):
        row = await repo.one(
            """
            select p.id as post_id,v.id as version_id,v.title,v.summary,v.body,v.sources,v.claims,
              v.topic_slugs,v.correction_note,
              case when r.conversation_id is null and r.payload->>'autonomous'='true' then r.report
              else jsonb_build_object('answer',v.body,'claims',v.claims,'limitations','[]'::jsonb,'verified',true) end as report
            from posts p
            join post_versions v on v.id=p.current_version_id
            join research_jobs r on r.id=v.research_job_id
            where p.id=%s and p.status='published' and v.status='approved'
            """,
            (job["post_id"],),
        )
        if row:
            post = {
                "title": row["title"][:180],
                "summary": row["summary"][:500],
                "body": row["body"][:6000],
                "sources": row["sources"][:6],
                "post_id": str(row.get("post_id", job["post_id"])),
                "version_id": str(row.get("version_id", "")),
                "claims": row.get("claims", [])[:20],
                "claim_sources": [
                    {key: source[key] for key in ("id", "url", "title")}
                    for source in row["sources"]
                ],
                "topic_tags": row.get("topic_slugs", []),
                "research_report": {
                    "answer": row.get("report", {}).get("answer", "")[:4000],
                    "claims": row.get("report", {}).get("claims", [])[:30],
                    "limitations": row.get("report", {}).get("limitations", []),
                    "verified": row.get("report", {}).get("verified", False),
                },
                "update_history": await repo.many(
                    """select id,previous_version_id,correction_note,created_at from post_versions
                    where post_id=%s and status='approved' order by created_at desc limit 5""",
                    (job["post_id"],),
                ),
                "suggested_questions": await repo.many(
                    "select prompt from post_followups where version_id=%s order by position",
                    (row.get("version_id"),),
                ),
            }
    return {"history": history, "history_excerpt": history_excerpt, "post": post}


def reference_context(state):
    """Context clarifies references; it does not establish fresh factual evidence."""
    post = state.get("post")
    return json.dumps(
        {
            "recent_conversation": state.get("history", [])[-4:],
            "earlier_turn_excerpts": state.get("history_excerpt", ""),
            "selected_post": {key: post[key] for key in ("title", "summary")} if post else None,
        },
        ensure_ascii=False,
    )
