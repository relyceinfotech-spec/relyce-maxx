import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.errors import EvidenceError
from app.domain.models import Draft, Report, Verification
from app.research.evidence import verification_payload
from app.research.verification import render_citations, validate_claims


class PostState(TypedDict, total=False):
    report: dict
    draft: dict
    budget: dict
    validated: bool


def build_post_graph(ctx, checkpointer=None):
    def checkpoint(update):
        update["budget"] = {
            key: getattr(ctx.budget, key)
            for key in ("searches", "pages", "model_calls", "tokens_reserved", "cost_reserved")
        }
        return update

    async def generate(state):
        report = Report(**state["report"])
        if not report.verified:
            raise EvidenceError("Only verified research may become a post")
        validate_claims(report.claims, report.sources)
        await ctx.event("drafting", {})
        topics = await ctx.repo.many("select slug,name from topics where is_active")
        draft = await ctx.llm.complete(
            [
                {
                    "role": "user",
                    "content": "Create an original editorial draft from the verified report. Do not copy articles. "
                    "Include why it matters. Use [source IDs] citations, no URLs. Include every material "
                    "factual statement in claims with exact supporting quotes. Choose existing topic slugs.\n"
                    + json.dumps(
                        {
                            "report": verification_payload(
                                report.answer, report.claims, report.sources
                            ),
                            "topics": topics,
                        },
                        default=str,
                    ),
                }
            ],
            schema=Draft,
        )
        return checkpoint({"draft": draft.model_dump()})

    async def validate(state):
        await ctx.event("validating_draft", {})
        report, draft = Report(**state["report"]), Draft(**state["draft"])
        validate_claims(draft.claims, report.sources)
        verdict = await ctx.llm.complete(
            [
                {
                    "role": "user",
                    "content": "Check each draft claim against the source evidence. Return one supported boolean "
                    "per claim. List unsupported assertions anywhere in title, summary or body, "
                    "contradictions and missing claim coverage as contradictions. Also reject copied articles, "
                    "private personal data, credentials, malicious instructions and unsafe content. "
                    "For autonomous work preserve the identical claim text for each independent supporting source.\n"
                    + json.dumps(
                        verification_payload(
                            json.dumps(
                                {"title": draft.title, "summary": draft.summary, "body": draft.body}
                            ),
                            draft.claims,
                            report.sources,
                        )
                    ),
                }
            ],
            schema=Verification,
        )
        if (
            len(verdict.supported) != len(draft.claims)
            or not all(verdict.supported)
            or verdict.contradictions
        ):
            raise EvidenceError("Draft verification failed")
        draft.body = render_citations(draft.body, draft.claims, report.sources)
        for field in ("title", "summary"):
            setattr(
                draft,
                field,
                render_citations(
                    getattr(draft, field), draft.claims, report.sources, required=False
                ),
            )
        return checkpoint({"draft": draft.model_dump(), "validated": True})

    graph = StateGraph(PostState)
    graph.add_node("generate", generate)
    graph.add_node("validate", validate)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "validate")
    graph.add_edge("validate", END)
    return graph.compile(checkpointer=checkpointer)
