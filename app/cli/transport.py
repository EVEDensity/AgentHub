"""Authenticated HTTP transport shared by CLI control-plane APIs."""

from __future__ import annotations

import time
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

import httpx


class HttpTransport:
    """Small request boundary with bounded GET retries and token handling."""

    def __init__(self, base_url: str, timeout: float = 30.0, *, retries: int = 2) -> None:
        if timeout <= 0 or retries < 0:
            raise ValueError("timeout must be positive and retries non-negative")
        self.client = httpx.Client(base_url=base_url, timeout=timeout)
        self.retries = retries
        self._token: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        if not self._token:
            raise RuntimeError("not logged in")
        return {"Authorization": f"Bearer {self._token}"}

    def set_token(self, token: str) -> None:
        if not token.strip():
            raise ValueError("token must be non-empty")
        self._token = token

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers = {**self.headers, **headers}
        attempts = self.retries + 1 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.client.request(method, url, headers=headers, **kwargs)
                if response.status_code >= 500 and attempt + 1 < attempts:
                    time.sleep(0.1 * (2**attempt))
                    continue
                return response
            except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                if attempt + 1 >= attempts:
                    raise
                time.sleep(0.1 * (2**attempt))
        raise RuntimeError("HTTP request retry loop exhausted")

    @contextmanager
    def stream(self, method: str, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers = {**self.headers, **headers}
        with self.client.stream(method, url, headers=headers, **kwargs) as response:
            yield response


__all__ = ["HttpTransport"]
