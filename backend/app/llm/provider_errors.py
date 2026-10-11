"""Bounded, credential-redacted diagnostics; never retain raw provider metadata."""

import re

import httpx


def provider_error(response: httpx.Response) -> dict:
    result = {"http_status": response.status_code, "category": "provider_error"}
    try:
        error = response.json().get("error", {})
        message = error.get("message", "") if isinstance(error, dict) else ""
    except (ValueError, AttributeError):
        message = ""
    if not isinstance(message, str):
        message = ""
    # Strip credentials, links, email addresses and opaque identifiers before recording.
    message = re.sub(
        r"(?i)bearer\s+\S+|sk-[\w-]+|https?://\S+|\S+@\S+|[\w-]{32,}", "[redacted]", message
    )
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)[:500]
    if response.status_code == 402:
        lower = message.lower()
        result["category"] = (
            "token_limit_exceeds_credit"
            if "afford" in lower or "max_tokens" in lower
            else "insufficient_credits"
            if "credit" in lower or "balance" in lower
            else "billing_error"
        )
    result["message"] = message or "Provider returned no readable error message"
    return result
