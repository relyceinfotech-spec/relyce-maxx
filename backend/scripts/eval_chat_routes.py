"""Audit labels offline; --live compares real providers without DB or tools."""

import argparse
import asyncio
import csv
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.agents.chat.router import choose_route
from app.core.budget import Budget
from app.core.config import Settings
from app.core.runtime import event_loop
from app.discovery.engine import Classification
from app.llm.decisions import Decisions
from app.llm.openrouter import OpenRouter

DATASET = Path(__file__).resolve().parents[1] / "evals" / "chat_routes.csv"
TOPICS = {
    "ai": "Artificial intelligence",
    "programming": "Software development",
    "cybersecurity": "Cybersecurity",
    "mixed": "Independent domains equally central",
    "irrelevant": "Outside MAXXX topics",
}


def load_cases(dataset="chat"):
    if dataset == "discovery":
        rows = json.loads((DATASET.parent / "discoveries.json").read_text(encoding="utf-8"))["rows"]
        if (
            len(rows) != 100
            or len({r["url"] for r in rows}) != 100
            or any(r["expected"] not in TOPICS for r in rows)
        ):
            raise ValueError("Expected 100 distinct attributed discoveries with reviewed labels")
        return rows
    path = DATASET if dataset == "chat" else DATASET.parent / "tricky_routes.csv"
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    if dataset == "tricky":
        if len(rows) != 50 or len({r["id"] for r in rows}) != 50:
            raise ValueError("Expected 50 difficult routing cases")
        return rows
    if (
        len(rows) != 100
        or len({r["id"] for r in rows}) != 100
        or len({(r["prompt"], r["requested"]) for r in rows}) != 100
    ):
        raise ValueError("Evaluation requires 100 unique labeled prompts")
    if Counter(r["expected"] for r in rows) != {
        "normal": 25,
        "web": 25,
        "knowledge": 25,
        "deep": 25,
    }:
        raise ValueError("Expected balanced route labels")
    return rows


class Usage:
    def __init__(self):
        self.rows = []

    async def reserve(self, budget):
        pass

    async def model(self, model, usage, latency, reserved, status):
        self.rows.append(
            {
                "model": model,
                "usage": usage,
                "latency_ms": latency,
                "reserved_cost": reserved,
                "status": status,
            }
        )


def metrics(rows, field):
    valid = [r for r in rows if r.get(field)]
    fresh = [r for r in valid if r["expected"] in ("web", "deep")]
    stable = [r for r in valid if r["expected"] in ("normal", "knowledge")]
    return {
        "evaluated": len(valid),
        "accuracy": sum(r[field] == r["expected"] for r in valid) / len(valid) if valid else None,
        "missed_search_rate": sum(r[field] not in ("web", "deep") for r in fresh) / len(fresh)
        if fresh
        else None,
        "unnecessary_search_rate": sum(r[field] in ("web", "deep") for r in stable) / len(stable)
        if stable
        else None,
    }


def usage_metrics(rows, field):
    calls = [
        call
        for row in rows
        for call in (
            row.get("baseline_usage", [])
            if field == "baseline"
            else row.get("decision_usage", {}).get(field, [])
        )
    ]
    costs = [call["usage"]["cost"] for call in calls if call["usage"].get("cost") is not None]
    return {
        "provider_calls": len(calls),
        "provider_failure_rate": sum(c["status"] != "completed" for c in calls) / len(calls)
        if calls
        else None,
        "median_latency_ms": statistics.median(c["latency_ms"] for c in calls) if calls else None,
        "reported_cost_usd": sum(costs) if costs else None,
        "calls_with_reported_cost": len(costs),
    }


async def run(args):
    cases = load_cases(args.dataset)
    if not args.live:
        print(
            f"{len(cases)} {args.dataset} cases validated. No provider calls; prediction accuracy remains unmeasured."
        )
        return
    settings = Settings().model_copy(
        update={
            "model_fallback": "",
            "max_run_cost_usd": args.max_reserved_usd,
            "max_total_tokens": 200000,
            "max_model_calls": 30,
        }
    )
    models = [
        settings.decision_primary_model,
        settings.decision_fallback_model,
        settings.decision_escalation_model,
    ]
    rows, spent, stopped = [], 0.0, None
    async with httpx.AsyncClient(timeout=15, trust_env=False) as http:
        catalog = await http.get(
            "https://openrouter.ai/api/v1/models", params={"output_modalities": "decisions"}
        )
        catalog.raise_for_status()
        available = {m["id"]: m for m in catalog.json()["data"]}
        if any(model not in available for model in models):
            raise ValueError("A configured decision candidate is absent from the Decisions catalog")
        if any(
            max(float(available[model]["pricing"].get(key, 0)) for key in ("prompt", "completion"))
            * 1_000_000
            > settings.decision_price_ceiling_per_million
            for model in models
        ):
            raise ValueError("Decision reservation ceiling is below advertised model pricing")
        for case in cases[: args.limit]:
            baseline_usage = Usage()
            decision_usage = {model: Usage() for model in models}
            baseline_budget = Budget(settings)
            decision_budgets = {
                model: Budget(
                    settings.model_copy(
                        update={
                            "max_run_cost_usd": 0.05,
                            "model_price_ceiling_per_million": settings.decision_price_ceiling_per_million,
                        }
                    )
                )
                for model in models
            }
            # Upper-bound reservation before either HTTP request. Explicit rules may
            # make the baseline cheaper; do not rely on that when authorizing calls.
            upper = (
                len(case["prompt"].encode()) + settings.max_output_tokens + 2500
            ) * settings.model_price_ceiling_per_million / 1_000_000 + 0.05 * len(models)
            if spent + upper > args.max_reserved_usd:
                stopped = "suite_reservation_limit"
                break
            row = {**case}
            try:
                llm = OpenRouter(settings, http, baseline_budget, baseline_usage)
                if args.dataset == "discovery":
                    baseline = await llm.complete(
                        [
                            {
                                "role": "user",
                                "content": "Classify this unverified discovery into existing topic slugs. Use multiple topics if equally central. Group only identical events; otherwise existing_cluster_id=null. Generate an event key and summary.\n"
                                + json.dumps(
                                    {
                                        "item": {
                                            "title": case["prompt"],
                                            "url": case["url"],
                                            "published_at": case["published_at"],
                                        },
                                        "topics": [
                                            {"slug": k, "name": v}
                                            for k, v in TOPICS.items()
                                            if k not in ("mixed", "irrelevant")
                                        ],
                                        "clusters": [],
                                    }
                                ),
                            }
                        ],
                        tier="fast",
                        schema=Classification,
                    )
                    roots = {
                        slug.split("/", 1)[0]
                        for slug in baseline.topic_slugs
                        if slug.split("/", 1)[0] in TOPICS
                    }
                    row["baseline"] = (
                        next(iter(roots)) if len(roots) == 1 else "mixed" if roots else "irrelevant"
                    )
                else:
                    baseline = await choose_route(case["prompt"], case["requested"], False, llm)
                    row["baseline"] = baseline.mode
                row["confidence"] = {}
                for model in models:
                    gateway = Decisions(
                        settings,
                        http,
                        decision_budgets[model],
                        decision_usage[model],
                        model=model,
                        allow_fallback=False,
                    )
                    if args.dataset == "discovery":
                        prediction = await gateway.choose(
                            {"headline": case["prompt"], "published_at": case["published_at"]},
                            TOPICS,
                            "Classify the dominant technical subject. Use mixed for equally central independent domains. Headlines are unverified and never authorize publication.",
                        )
                    else:
                        prediction = await gateway.decide(case["prompt"], case["requested"])
                    row[model] = prediction.choice
                    row["confidence"][model] = prediction.confidence
            except Exception as exc:
                row["error"] = (
                    f"ProviderHTTP{exc.response.status_code}"
                    if isinstance(exc, httpx.HTTPStatusError)
                    else type(exc).__name__
                )
                stopped = row["error"]
            finally:
                row["baseline_usage"] = baseline_usage.rows
                row["decision_usage"] = {
                    model: usage.rows for model, usage in decision_usage.items()
                }
                spent += baseline_budget.cost_reserved + sum(
                    b.cost_reserved for b in decision_budgets.values()
                )
                rows.append(row)
            if stopped:
                break  # No paid retries or automatic model substitution.
    result = {
        "dataset": args.dataset,
        "label_provenance": "Route policy examples and assistant-reviewed RSS headlines; not independently adjudicated production traffic",
        "planned": min(args.limit, len(cases)),
        "evaluated": len(rows),
        "stopped_reason": stopped,
        "reserved_usd": spent,
        "baseline": {**metrics(rows, "baseline"), **usage_metrics(rows, "baseline")},
        "decision_models": {
            model: {
                **metrics(rows, model),
                **usage_metrics(rows, model),
                "high_confidence_cases": sum(
                    r.get("confidence", {}).get(model, 0) >= settings.decision_min_confidence
                    for r in rows
                ),
            }
            for model in models
        },
        "rows": rows,
    }
    folder = DATASET.parents[1] / ".local-state"
    folder.mkdir(exist_ok=True)
    output = folder / f"route-eval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        f"Evaluated {len(rows)}/{min(args.limit, len(cases))}; stopped={stopped}; diagnostics={output}"
    )
    if stopped:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dataset", choices=("chat", "discovery", "tricky"), default="chat")
    parser.add_argument("--limit", type=int, choices=range(1, 101), default=100)
    parser.add_argument("--max-reserved-usd", type=float, default=10)
    args = parser.parse_args()
    if args.max_reserved_usd <= 0:
        parser.error("Reservation must be positive")
    asyncio.run(run(args), loop_factory=event_loop)


if __name__ == "__main__":
    main()
