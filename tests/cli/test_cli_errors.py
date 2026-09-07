import httpx

from app.cli.errors import (
    CliErrorKind,
    EXIT_FAILURE,
    EXIT_INFRASTRUCTURE,
    EXIT_PERMISSION,
    EXIT_TIMEOUT,
    EXIT_USAGE,
    classify_error,
    error_exit_code,
    to_error_envelope,
)


def test_error_taxonomy_classifies_transport_and_timeout():
    assert classify_error(httpx.ConnectError("down")) == CliErrorKind.TRANSPORT
    assert classify_error(httpx.ReadTimeout("slow")) == CliErrorKind.TIMEOUT


def test_error_taxonomy_classifies_protocol_and_unknown():
    assert classify_error(ValueError("bad json")) == CliErrorKind.PROTOCOL
    assert classify_error(RuntimeError("unexpected")) == CliErrorKind.UNKNOWN


def test_error_taxonomy_classifies_http_status_from_response():
    request = httpx.Request("POST", "https://provider.test")
    assert classify_error(httpx.HTTPStatusError("rate limited", request=request, response=httpx.Response(429, request=request))) == CliErrorKind.PROVIDER
    assert classify_error(httpx.HTTPStatusError("upstream", request=request, response=httpx.Response(503, request=request))) == CliErrorKind.TRANSPORT
    assert classify_error(httpx.HTTPStatusError("unauthorized", request=request, response=httpx.Response(401, request=request))) == CliErrorKind.AUTH
    assert classify_error(httpx.HTTPStatusError("invalid", request=request, response=httpx.Response(422, request=request))) == CliErrorKind.VALIDATION
    assert classify_error(httpx.HTTPStatusError("timeout", request=request, response=httpx.Response(408, request=request))) == CliErrorKind.TIMEOUT


def test_error_envelope_distinguishes_auth_permission_and_request_id():
    request = httpx.Request("GET", "https://example.test")
    auth = to_error_envelope(httpx.HTTPStatusError("unauthorized", request=request, response=httpx.Response(401, request=request)), request_id="req-1")
    denied = to_error_envelope(httpx.HTTPStatusError("forbidden", request=request, response=httpx.Response(403, request=request)), request_id="req-2")
    assert auth.to_dict()["category"] == "auth"
    assert denied.to_dict()["category"] == "permission"
    assert auth.to_dict()["requestId"] == "req-1"
    assert error_exit_code(auth) == EXIT_INFRASTRUCTURE
    assert error_exit_code(denied) == EXIT_PERMISSION


def test_error_exit_code_matches_production_categories():
    assert error_exit_code(to_error_envelope(ValueError("bad"))) == EXIT_USAGE
    assert error_exit_code(to_error_envelope(TimeoutError("slow"))) == EXIT_TIMEOUT
    request = httpx.Request("GET", "https://example.test")
    rate = to_error_envelope(httpx.HTTPStatusError("rate", request=request, response=httpx.Response(429, request=request)))
    assert error_exit_code(rate) == EXIT_FAILURE
