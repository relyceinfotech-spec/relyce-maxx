from dataclasses import dataclass

from app.core.budget import Budget
from app.llm.credits import check_credit
from app.llm.decisions import Decisions
from app.llm.openrouter import OpenRouter
from app.observability.tracing import Trace
from app.retrieval.knowledge import Knowledge
from app.tools.fetcher import Fetcher
from app.tools.serper import Serper


@dataclass
class Context:
    settings: object
    repo: object
    job: dict
    budget: Budget
    trace: Trace
    llm: OpenRouter
    search: Serper
    fetcher: Fetcher
    knowledge: Knowledge
    decisions: Decisions | None = None
    evidence_diagnostics: object | None = None

    async def event(self, stage, data):
        await self.repo.event(self.job, stage, data)


def make_context(settings, repo, http, job, run_id):
    budget = Budget(settings, deep=job["mode"] == "deep")
    trace = Trace(repo, run_id)
    trace.job = job
    for key, value in job.get("budget", {}).items():
        setattr(budget, key, value)
    return Context(
        settings,
        repo,
        job,
        budget,
        trace,
        OpenRouter(
            settings,
            http,
            budget,
            trace,
            chat_workflow=job.get("kind") != "draft"
            and not job.get("payload", {}).get("autonomous"),
        ),
        Serper(
            settings,
            http,
            budget,
            trace,
            credit_preflight=(lambda: check_credit(settings, http, trace))
            if settings.provider_credit_preflight_enabled
            and job.get("kind") != "draft"
            and not job.get("payload", {}).get("autonomous")
            else None,
        ),
        Fetcher(settings),
        Knowledge(repo, settings, http, budget, trace),
        Decisions(
            settings,
            http,
            Budget(
                settings.model_copy(
                    update={
                        "max_run_cost_usd": 0.05,
                        "model_price_ceiling_per_million": settings.decision_price_ceiling_per_million,
                    }
                )
            ),
            Trace(repo, run_id),
        )
        if settings.decision_mode == "shadow" and not job.get("payload", {}).get("autonomous")
        else None,
    )
