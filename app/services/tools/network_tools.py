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


# ── web_search (north-star M1) ────────────────────────────────────────────

WEB_SEARCH_TIMEOUT = 20  # seconds
WEB_SEARCH_MAX_RESULTS = 8
WEB_SEARCH_RESULT_SNIPPET_CHARS = 600
TAVILY_API_KEY_ENV = "AGENTHUB_TAVILY_API_KEY"
_DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


async def web_search_handler(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the public web and return bounded results.

    Backend resolution:

    - ``AGENTHUB_TAVILY_API_KEY`` set → Tavily Search API (preferred);
    - otherwise → DuckDuckGo HTML endpoint (no key required, best effort).

    Failures return ``{"success": False, "error": ...}`` — never a
    synthetic result set.
    """
    import os as _os

    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "搜索关键词不能为空"}
    limit = min(max(int(max_results), 1), WEB_SEARCH_MAX_RESULTS)

    api_key = _os.environ.get(TAVILY_API_KEY_ENV, "").strip()
    if api_key:
        return await _tavily_search(query, api_key, limit)
    return await _ddg_html_search(query, limit)


async def _tavily_search(query: str, api_key: str, limit: int) -> dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=WEB_SEARCH_TIMEOUT) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": limit,
                    "include_answer": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException:
        return {"success": False, "error": f"搜索超时（{WEB_SEARCH_TIMEOUT}秒）"}
    except Exception as exc:
        logger.warning("tavily search failed: %s", exc)
        return {"success": False, "error": f"Tavily 搜索失败: {exc}"}

    results = []
    for item in payload.get("results", [])[:limit]:
        results.append(
            {
                "title": _clip(str(item.get("title") or ""), 200),
                "url": str(item.get("url") or ""),
                "snippet": _clip(
                    str(item.get("content") or ""), WEB_SEARCH_RESULT_SNIPPET_CHARS
                ),
            }
        )
    if not results:
        return {"success": False, "error": "搜索无结果"}
    return {"success": True, "results": results, "backend": "tavily"}


async def _ddg_html_search(query: str, limit: int) -> dict[str, Any]:
    """Keyless DuckDuckGo HTML search. Best effort: the endpoint may rate
    limit or change markup, in which case the failure is reported honestly."""
    import re as _re

    import httpx

    try:
        async with httpx.AsyncClient(timeout=WEB_SEARCH_TIMEOUT) as client:
            response = await client.post(
                _DDG_HTML_ENDPOINT,
                data={"q": query, "kl": "wt-wt"},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AgentHub/4.0)",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response.raise_for_status()
            html = response.text
    except httpx.TimeoutException:
        return {"success": False, "error": f"搜索超时（{WEB_SEARCH_TIMEOUT}秒）"}
    except Exception as exc:
        logger.warning("duckduckgo search failed: %s", exc)
        return {"success": False, "error": f"DuckDuckGo 搜索失败: {exc}"}

    # Zero-dependency parse: each organic result is an <a class="result__a">
    # plus an optional <a class="result__snippet">.
    link_pattern = _re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        _re.DOTALL,
    )
    snippet_pattern = _re.compile(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', _re.DOTALL
    )
    tag_pattern = _re.compile(r"<[^>]+>")
    entity_map = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "#39": "'"}
    entity_pattern = _re.compile(r"&([a-z0-9#]+);")

    def _clean(fragment: str) -> str:
        text = tag_pattern.sub("", fragment)
        text = entity_pattern.sub(
            lambda m: entity_map.get(m.group(1), m.group(0)), text
        )
        return text.strip()

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)
    results = []
    seen_urls: set[str] = set()
    for index, (href, title_html) in enumerate(links):
        # DuckDuckGo wraps URLs in a redirect; extract the direct target.
        url = href
        if "uddg=" in href:
            from urllib.parse import parse_qs, unquote, urlparse

            try:
                query_params = parse_qs(urlparse(href).query)
                url = unquote(query_params.get("uddg", [href])[0])
            except Exception:
                url = href
        if url in seen_urls:
            continue
        seen_urls.add(url)
        title = _clean(title_html)
        if not title:
            continue
        snippet = (
            _clean(snippets[index]) if index < len(snippets) else ""
        )
        results.append(
            {
                "title": _clip(title, 200),
                "url": url,
                "snippet": _clip(snippet, WEB_SEARCH_RESULT_SNIPPET_CHARS),
            }
        )
        if len(results) >= limit:
            break
    if not results:
        return {
            "success": False,
            "error": "搜索无结果（DuckDuckGo HTML 端点可能被限流，稍后重试）",
        }
    return {"success": True, "results": results, "backend": "duckduckgo"}


# ── web_fetch (north-star §2 / I-6a) ──────────────────────────────────────
#
# The browser-side complement to web_search: instead of a result list,
# it retrieves one public URL and extracts readable text (HTML →
# stripped text with title; plain text / JSON passed through). The
# capability boundary is the same as web_search — the same SSRF rules
# as http_request, the same desktop gatekeeping switch — so enabling
# web tools exposes both together.

WEB_FETCH_TIMEOUT = 20  # seconds
WEB_FETCH_MAX_CHARS = 20_000
WEB_FETCH_MAX_REDIRECTS = 5


def _html_to_text(html: str) -> tuple[str, str]:
    """Extract (title, readable text) from an HTML page, dependency-free.

    Best-effort: drops script/style blocks, tags, and collapses
    whitespace. Rendering JavaScript or visual structure is out of
    scope — this is a reader, not a browser engine.
    """
    import re as _re

    entity_map = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "#39": "'", "nbsp": " "}

    def _decode(text: str) -> str:
        return _re.sub(
            r"&([a-z0-9#]+);", lambda m: entity_map.get(m.group(1), m.group(0)), text
        )

    title = ""
    title_match = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.DOTALL | _re.IGNORECASE)
    if title_match:
        title = _clip(_decode(_re.sub(r"<[^>]+>", "", title_match.group(1))), 300)

    text = _re.sub(
        r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>",
        " ",
        html,
        flags=_re.DOTALL | _re.IGNORECASE,
    )
    # Block-level tags become separators so words from adjacent
    # elements do not run together.
    text = _re.sub(r"</?(p|div|br|li|tr|h[1-6]|section|article|blockquote)[^>]*>", "\n", text, flags=_re.IGNORECASE)
    text = _re.sub(r"<[^>]+>", " ", text)
    text = _decode(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return title, "\n".join(line for line in lines if line)


async def web_fetch_handler(url: str, max_chars: int = WEB_FETCH_MAX_CHARS) -> dict[str, Any]:
    """Fetch one public URL and return readable text content.

    Gated by the same desktop web-tools switch as web_search. HTML
    pages are converted to stripped text (title + body); plain text and
    JSON pass through. SSRF rules are identical to http_request.
    Failures return ``{"success": False, "error": ...}`` — never a
    synthetic document.
    """
    import httpx

    url = (url or "").strip()
    if not url:
        return {"success": False, "error": "URL 不能为空"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    allowed, reason = _is_host_allowed(url)
    if not allowed:
        return {"success": False, "error": reason}

    limit = min(max(int(max_chars), 200), WEB_FETCH_MAX_CHARS)

    try:
        async with httpx.AsyncClient(
            timeout=WEB_FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=WEB_FETCH_MAX_REDIRECTS,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AgentHub/4.0)"},
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return {"success": False, "error": f"抓取超时（{WEB_FETCH_TIMEOUT}秒）"}
    except httpx.TooManyRedirects:
        return {"success": False, "error": f"重定向次数超过 {WEB_FETCH_MAX_REDIRECTS} 次"}
    except Exception as exc:
        logger.warning("web_fetch failed: %s", exc)
        return {"success": False, "error": f"抓取失败: {exc}"}

    if response.status_code >= 400:
        return {
            "success": False,
            "error": f"目标返回 HTTP {response.status_code}",
            "metadata": {"url": url, "status_code": response.status_code},
        }

    content_type = response.headers.get("content-type", "").lower()
    raw = response.text
    if "html" in content_type:
        title, text = _html_to_text(raw)
        content_kind = "html"
    else:
        title, text = "", raw
        content_kind = "json" if "json" in content_type else "text"

    truncated = len(text) > limit
    if truncated:
        text = text[:limit]

    result: dict[str, Any] = {
        "url": str(response.url),
        "content_type": content_type,
        "kind": content_kind,
        "content": text,
        "truncated": truncated,
    }
    if title:
        result["title"] = title
    return {"success": True, "result": result}
