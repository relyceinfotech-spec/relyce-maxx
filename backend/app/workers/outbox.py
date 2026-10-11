import logging

from app.core.auth import User
from app.domain.models import PostFromResearch
from app.services.jobs import Jobs


async def deliver_outbox(repo):
    # Read and deliver without holding a pool connection across service calls.
    # Concurrent delivery is safe: from_research has a deterministic key.
    item = await repo.one("""
        select * from agent_private.outbox where status='pending'
        order by created_at limit 1
    """)
    if item:
        try:
            await Jobs(repo).from_research(
                User(item["user_id"], True), PostFromResearch(**item["payload"])
            )
            await repo.execute(
                "update agent_private.outbox set status='delivered' where job_id=%s",
                (item["job_id"],),
            )
        except Exception as exc:
            logging.getLogger("maxxx.worker").error("outbox_failed category=%s", type(exc).__name__)
            await repo.execute(
                "update agent_private.outbox set status='failed',error_code=%s where job_id=%s",
                (type(exc).__name__, item["job_id"]),
            )
