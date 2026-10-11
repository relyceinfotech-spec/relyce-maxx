import httpx

from app.llm.provider_errors import provider_error


def test_credit_diagnostic_redacts_credentials_and_ignores_metadata():
    response = httpx.Response(
        402,
        json={
            "error": {
                "message": "max_tokens exceeds balance; can afford 42 tokens. Bearer private-key sk-or-private https://secret.invalid/key",
                "metadata": {"headers": {"Authorization": "secret"}},
            }
        },
    )
    result = provider_error(response)
    assert result["category"] == "token_limit_exceeds_credit"
    assert "42 tokens" in result["message"]
    assert "private" not in str(result) and "Authorization" not in str(result)


def test_unknown_billing_body_does_not_invent_credit_reason():
    assert provider_error(httpx.Response(402, text="not json"))["category"] == "billing_error"
    assert (
        provider_error(httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))[
            "category"
        ]
        == "insufficient_credits"
    )
