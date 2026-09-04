import httpx

from app.cli.errors import CliErrorKind, classify_error


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
