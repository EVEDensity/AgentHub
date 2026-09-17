"""Authenticated HTTP transport shared by CLI control-plane APIs."""

from __future__ import annotations

import time
import random
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

import httpx


class HttpTransport:
    """Shared HTTP boundary with auth, request IDs, timeouts, and safe retries.

    Only idempotent methods are retried. Decision resolution and side-effect
    commands therefore never receive an implicit duplicate request.
    """

    _IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        *,
        retries: int = 2,
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if timeout <= 0 or retries < 0:
            raise ValueError("timeout must be positive and retries non-negative")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        self.timeout_seconds = float(timeout)
        self.max_response_bytes = int(max_response_bytes)
        self.client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=min(10.0, self.timeout_seconds),
                read=self.timeout_seconds,
                write=self.timeout_seconds,
                pool=min(10.0, self.timeout_seconds),
            ),
        )
        self.retries = retries
        self._token: str | None = None
        self.last_request_id: str = ""

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

    @staticmethod
    def new_request_id() -> str:
        """Return a non-secret operation identifier for logs and receipts."""
        return f"req-{uuid.uuid4().hex}"

    def _prepare_headers(
        self,
        headers: dict[str, str] | None,
        *,
        require_auth: bool = True,
    ) -> dict[str, str]:
        merged = {**(self.headers if require_auth else {}), **(headers or {})}
        self.last_request_id = str(merged.get("X-Request-ID") or self.new_request_id())
        merged["X-Request-ID"] = self.last_request_id
        return merged

    def _check_response_size(self, response: httpx.Response) -> None:
        headers = getattr(response, "headers", {}) or {}
        value = headers.get("content-length")
        try:
            if value is not None and int(value) > self.max_response_bytes:
                response.close()
                raise ValueError(
                    f"HTTP response exceeds {self.max_response_bytes} bytes"
                )
        except ValueError as exc:
            if "exceeds" in str(exc):
                raise

    @staticmethod
    def _retryable_status(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        method_upper = method.upper()
        require_auth = bool(kwargs.pop("require_auth", True))
        headers = self._prepare_headers(
            kwargs.pop("headers", None), require_auth=require_auth
        )
        attempts = self.retries + 1 if method_upper in self._IDEMPOTENT_METHODS else 1
        for attempt in range(attempts):
            try:
                response = self.client.request(method, url, headers=headers, **kwargs)
                self._check_response_size(response)
                if self._retryable_status(response.status_code) and attempt + 1 < attempts:
                    # The response is intentionally discarded before the
                    # retry.  Closing it releases the connection back to the
                    # pool and prevents retries from exhausting sockets when
                    # an upstream is continuously unavailable.
                    response.close()
                    delay = min(2.0, 0.1 * (2**attempt)) + random.uniform(0, 0.05)
                    time.sleep(delay)
                    continue
                return response
            except (httpx.ConnectError, httpx.ReadError, httpx.NetworkError, httpx.TimeoutException):
                if attempt + 1 >= attempts:
                    raise
                delay = min(2.0, 0.1 * (2**attempt)) + random.uniform(0, 0.05)
                time.sleep(delay)
        raise RuntimeError("HTTP request retry loop exhausted")

    @contextmanager
    def stream(self, method: str, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
        headers = self._prepare_headers(kwargs.pop("headers", None))
        with self.client.stream(method, url, headers=headers, **kwargs) as response:
            self._check_response_size(response)
            yield response


__all__ = ["HttpTransport"]
