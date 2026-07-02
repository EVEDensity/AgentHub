from __future__ import annotations

"""Network / HTTP tools for making external API calls.

Provides ``http_request`` — a generic HTTP client tool that allows
the LLM to query external APIs, trigger webhooks, and interact with
third-party services.
"""

import logging
from typing import Any

logger = logging.getLogger("agenthub.tools.network")

# ── Security constraints ──────────────────────────────────────────────────

HTTP_REQUEST_TIMEOUT = 30  # seconds
MAX_RESPONSE_CHARS = 50_000
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Block internal/private IP ranges to prevent SSRF
BLOCKED_HOSTS = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "169.254.0.0/16",  # link-local
})
BLOCKED_SUFFIXES = (".local", ".internal", ".localhost")


def _is_host_allowed(url_str: str) -> tuple[bool, str]:
    """Check if a URL's host is allowed (prevent SSRF).

    Returns (allowed, reason).
    """
    import re as _re
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url_str)
    except Exception:
        return False, "无法解析 URL"

    if parsed.scheme not in ALLOWED_SCHEMES:
        return False, f"不支持的协议: {parsed.scheme}"

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, "URL 中没有有效的主机名"

    # Block IP addresses in private ranges
    import ipaddress
    try:
        ip = ipaddress.ip_address(hostname)
        for blocked in BLOCKED_HOSTS:
            if "/" in blocked:
                if ip in ipaddress.ip_network(blocked):
                    return False, f"禁止访问内网地址: {hostname}"
            elif str(ip) == blocked:
                return False, f"禁止访问内网地址: {hostname}"
        return True, ""
    except ValueError:
        pass  # Not an IP address — check hostname

    if hostname in BLOCKED_HOSTS:
        return False, f"禁止访问内网地址: {hostname}"

    if hostname.endswith(BLOCKED_SUFFIXES):
        return False, f"禁止访问内网地址: {hostname}"

    return True, ""


async def http_request_handler(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Make an HTTP request to an external URL.

    Supports GET, POST, PUT, DELETE, PATCH methods.  Returns response
    status, headers, and body.  Internal/private IP addresses are blocked
    for security (SSRF prevention).

    Args:
        url: Target URL (must be https:// or http://).
        method: HTTP method — GET, POST, PUT, DELETE, PATCH.
        headers: Request headers as a dict (e.g. ``{"Authorization": "Bearer ..."}``).
        body: Request body text (for POST/PUT/PATCH).
        timeout: Request timeout in seconds (default 30, max 30).

    Returns:
        Response with status_code, headers, body, duration_ms.
    """
    import time as _time_module
    import httpx
    from app.services.adapter_manager import _get_client

    if not url or not url.strip():
        return {"success": False, "error": "URL 不能为空"}

    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Validate HTTP method
    valid_methods = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})
    method_upper = method.strip().upper()
    if method_upper not in valid_methods:
        return {"success": False, "error": f"不支持的 HTTP 方法: {method}。支持: {', '.join(sorted(valid_methods))}"}

    # Security: block internal hosts
    allowed, reason = _is_host_allowed(url)
    if not allowed:
        return {"success": False, "error": reason}

    effective_timeout = min(max(timeout, 1), HTTP_REQUEST_TIMEOUT)

    # Parse headers
    request_headers: dict[str, str] = {
        "User-Agent": "AgentHub/4.0",
    }
    if headers:
        for k, v in headers.items():
            request_headers[str(k).strip()] = str(v).strip()

    start = _time_module.time()

    try:
        client = _get_client()
        timeout_obj = httpx.Timeout(effective_timeout)

        if method_upper == "GET":
            resp = await client.get(url, headers=request_headers, timeout=timeout_obj)
        elif method_upper == "POST":
            resp = await client.post(url, headers=request_headers, content=body or None, timeout=timeout_obj)
        elif method_upper == "PUT":
            resp = await client.put(url, headers=request_headers, content=body or None, timeout=timeout_obj)
        elif method_upper == "DELETE":
            resp = await client.delete(url, headers=request_headers, timeout=timeout_obj)
        elif method_upper == "PATCH":
            resp = await client.patch(url, headers=request_headers, content=body or None, timeout=timeout_obj)
        elif method_upper == "HEAD":
            resp = await client.head(url, headers=request_headers, timeout=timeout_obj)
        elif method_upper == "OPTIONS":
            resp = await client.options(url, headers=request_headers, timeout=timeout_obj)
        else:
            return {"success": False, "error": f"不支持的 HTTP 方法: {method_upper}"}

        duration_ms = (_time_module.time() - start) * 1000

        # Extract response
        response_body = resp.text[:MAX_RESPONSE_CHARS]
        truncated = len(resp.text) > MAX_RESPONSE_CHARS

        # Parse JSON if possible
        response_json = None
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type and response_body:
            try:
                import json as _json
                response_json = _json.loads(response_body)
            except Exception:
                pass

        result_data: dict[str, Any] = {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": response_body,
            "body_truncated": truncated,
            "duration_ms": round(duration_ms, 1),
            "url": str(resp.url),
        }
        if response_json is not None:
            result_data["json"] = response_json

        success = 200 <= resp.status_code < 400

        return {
            "success": success,
            "result": result_data,
            "metadata": {
                "method": method_upper,
                "url": url,
                "status_code": resp.status_code,
                "duration_ms": round(duration_ms, 1),
                "body_length": len(response_body),
            },
        }

    except httpx.TimeoutException:
        duration_ms = (_time_module.time() - start) * 1000
        return {
            "success": False,
            "error": f"HTTP 请求超时（{effective_timeout}秒）",
            "metadata": {"url": url, "duration_ms": round(duration_ms, 1)},
        }
    except httpx.ConnectError as exc:
        return {
            "success": False,
            "error": f"无法连接到服务器: {exc}",
            "metadata": {"url": url},
        }
    except Exception as exc:
        logger.exception("http_request failed url=%s", url)
        return {
            "success": False,
            "error": f"HTTP 请求失败: {exc}",
            "metadata": {"url": url},
        }
