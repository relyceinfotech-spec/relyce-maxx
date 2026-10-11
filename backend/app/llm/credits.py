"""Read-only known-key-budget gate. Account balance may be unknown."""

from decimal import Decimal, InvalidOperation

from app.core.errors import ProviderCreditUnavailable


async def check_credit(settings, http, trace):
    async with trace.tool("provider_credit_check"):
        response = await http.get(
            "https://openrouter.ai/api/v1/key",
            headers={
                "Authorization": "Bearer " + settings.openrouter_api_key.get_secret_value(),
            },
        )
        response.raise_for_status()
        data = response.json()["data"]
        limits = [data.get("limit_remaining")]
        effective = data.get("effective_budget")
        if isinstance(effective, dict):
            limits.append(effective.get("remaining_usd"))
        limits.extend(
            b.get("remaining_usd") for b in data.get("budgets", []) if isinstance(b, dict)
        )
        for limit in limits:
            if limit is None:
                continue
            try:
                remaining = Decimal(str(limit))
            except InvalidOperation as exc:
                raise ProviderCreditUnavailable(
                    "Provider budget data invalid; search withheld"
                ) from exc
            if not remaining.is_finite() or remaining <= 0:
                raise ProviderCreditUnavailable(
                    "Provider spending limit exhausted; search withheld"
                )
        # No key cap is not proof of account balance or an affordable inference request.
        status = (
            "known_limits_positive"
            if any(v is not None for v in limits)
            else "account_balance_unknown"
        )
        if getattr(trace, "job", None):
            await trace.repo.event(
                trace.job,
                "provider_credit_preflight",
                {
                    "status": status,
                    "account_balance_verified": False,
                },
            )
        return status
