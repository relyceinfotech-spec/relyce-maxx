"""Provider-reported usage stays separate from conservative capacity reservations."""

from decimal import Decimal


def usage_summary(records, budget):
    costs = [
        Decimal(str(r["reported_cost"])) for r in records if r.get("reported_cost") is not None
    ]
    return {
        "reported_input_tokens": sum(r.get("prompt_tokens", 0) for r in records),
        "reported_output_tokens": sum(r.get("completion_tokens", 0) for r in records),
        "reported_model_cost_usd": str(sum(costs, Decimal(0))) if costs else None,
        "reported_cost_complete": bool(records) and len(costs) == len(records),
        "reserved_tokens": budget.get("tokens_reserved", 0),
        "reserved_model_cost_usd": budget.get("cost_reserved", 0),
        "search_cost_included": False,
    }
