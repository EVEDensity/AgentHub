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
