import math
from datetime import UTC, datetime


def rank(posts: list[dict], mode: str, limit: int, now=None):
    now = now or datetime.now(UTC)
    candidates = []
    for post in posts:
        if post["blocked"] or post["hidden"] or (mode == "following" and not post["interest"]):
            continue
        age = max(0, (now - post["published_at"]).total_seconds() / 3600)
        freshness = math.exp(-math.log(2) * age / 48)
        score = 0.55 * float(post["interest"] or 0) + 0.45 * freshness
        post = {
            **post,
            "ranking": {
                "score": round(score, 4),
                "interest": float(post["interest"] or 0),
                "freshness": round(freshness, 4),
            },
        }
        candidates.append(post)
    candidates.sort(
        key=lambda p: (
            (p["published_at"], str(p["id"]))
            if mode == "latest"
            else (p["ranking"]["score"], str(p["id"]))
        ),
        reverse=True,
    )
    if mode == "latest":
        return candidates[:limit]
    result, remaining = [], list(candidates)
    while remaining and len(result) < limit:
        # Soft diversity: avoid three consecutive posts with the same primary topic.
        index = 0
        if len(result) >= 2 and result[-1]["primary_topic"] == result[-2]["primary_topic"]:
            index = next(
                (
                    i
                    for i, p in enumerate(remaining)
                    if p["primary_topic"] != result[-1]["primary_topic"]
                ),
                0,
            )
        result.append(remaining.pop(index))
    return result


class Feed:
    def __init__(self, repo):
        self.repo = repo

    async def get(self, user_id, mode, limit):
        rows = await self.repo.many(
            """
            with recursive preferences as (
              select t.id,u.weight,u.blocked from user_interests u join topics t on t.id=u.topic_id
              where u.user_id=%s
              union all select t.id,p.weight,p.blocked from topics t join preferences p on t.parent_id=p.id
            ), matches as (
              select pt.post_id,max(case when not p.blocked then p.weight else 0 end) interest,
                bool_or(p.blocked) blocked from post_topics pt join preferences p on p.id=pt.topic_id
              group by pt.post_id
            ) select p.id,p.published_at,p.current_version_id,v.title,v.summary,v.body,
              coalesce(m.interest,0) interest,coalesce(m.blocked,false) blocked,
              exists(select 1 from user_interactions i where i.post_id=p.id and i.user_id=%s
                and i.kind in ('hide','not_interested')) hidden,
              (select t.slug from post_topics pt join topics t on t.id=pt.topic_id
                where pt.post_id=p.id and pt.is_primary) primary_topic,
              (select array_agg(t.slug) from post_topics pt join topics t on t.id=pt.topic_id
                where pt.post_id=p.id) topics
            from posts p join post_versions v on v.id=p.current_version_id
            left join matches m on m.post_id=p.id
            where p.status='published' and v.status='approved'
            order by p.published_at desc,p.id limit 300
        """,
            (user_id, user_id),
        )
        return {
            "items": rank(rows, mode, limit),
            "mode": mode,
            "candidate_limit": 300,
            "ranking_version": "explicit-interests-v1",
        }
