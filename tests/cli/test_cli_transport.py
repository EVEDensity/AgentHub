from unittest.mock import Mock

import httpx

from app.cli.transport import HttpTransport


def test_transport_requires_auth_and_merges_headers():
    transport = HttpTransport("http://test")
    try:
        try:
            transport.request("GET", "/health")
        except RuntimeError as exc:
            assert "not logged in" in str(exc)
        transport.set_token("token")
        response = Mock(spec=httpx.Response)
        response.status_code = 200
        transport.client.request = Mock(return_value=response)
        assert transport.request("GET", "/health", headers={"X-Test": "1"}) is response
        sent = transport.client.request.call_args.kwargs["headers"]
        assert sent["Authorization"] == "Bearer token"
        assert sent["X-Test"] == "1"
    finally:
        transport.close()


def test_transport_retries_transient_get_once():
    transport = HttpTransport("http://test", retries=1)
    try:
        transport.set_token("token")
        first = Mock(spec=httpx.Response)
        first.status_code = 503
        second = Mock(spec=httpx.Response)
        second.status_code = 200
        transport.client.request = Mock(side_effect=[first, second])
        assert transport.request("GET", "/health") is second
        assert transport.client.request.call_count == 2
    finally:
        transport.close()


def test_transport_injects_request_id_and_does_not_retry_post():
    transport = HttpTransport("http://test", retries=2)
    try:
        transport.set_token("token")
        response = Mock(spec=httpx.Response)
        response.status_code = 200
        response.headers = {}
        transport.client.request = Mock(return_value=response)
        transport.request("GET", "/health")
        sent = transport.client.request.call_args.kwargs["headers"]
        assert sent["X-Request-ID"].startswith("req-")
        assert transport.last_request_id == sent["X-Request-ID"]

        transport.client.request.reset_mock()
        transport.client.request.side_effect = httpx.ConnectError("down")
        try:
            transport.request("POST", "/mutate")
        except httpx.ConnectError:
            pass
        assert transport.client.request.call_count == 1
    finally:
        transport.close()


def test_transport_rejects_oversized_response_before_body_read():
    transport = HttpTransport("http://test", max_response_bytes=1024)
    try:
        transport.set_token("token")
        response = Mock(spec=httpx.Response)
        response.status_code = 200
        response.headers = {"content-length": "2048"}
        response.close = Mock()
        transport.client.request = Mock(return_value=response)
        try:
            transport.request("GET", "/large")
        except ValueError as exc:
            assert "exceeds" in str(exc)
        else:
            raise AssertionError("oversized response was accepted")
        response.close.assert_called_once()
    finally:
        transport.close()
