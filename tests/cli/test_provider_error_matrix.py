import httpx

from app.errors import provider_error_matrix


def test_provider_rate_limit_matrix_is_redacted_and_retryable():
    request = httpx.Request("POST", "https://provider.invalid")
    response = httpx.Response(429, request=request, headers={"retry-after": "10", "x-request-id": "upstream-secret"})
    value = provider_error_matrix(httpx.HTTPStatusError("rate", request=request, response=response))
    assert value == {"errorType": "rate_limited", "retryable": True, "statusCode": 429, "requestId": "<redacted>", "retryAfterSeconds": 10}


def test_provider_matrix_does_not_mark_non_idempotent_auth_retryable():
    request = httpx.Request("POST", "https://provider.invalid")
    value = provider_error_matrix(httpx.HTTPStatusError("auth", request=request, response=httpx.Response(401, request=request)))
    assert value["errorType"] == "authentication"
    assert value["retryable"] is False
