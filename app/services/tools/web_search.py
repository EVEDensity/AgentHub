"""Web-search tool with multi-provider fallback.

All search providers are dispatched through ``web_search_handler``.
Split out of ``builtin_tools.py``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agenthub.tools.builtin.web_search")

# ── web_search (multi-provider with mode-based selection) ────────────

# Valid WEB_SEARCH_MODE values (mirrors the TypeScript WebSearchMode)
_VALID_SEARCH_MODES = frozenset({
    "auto", "bing", "serpapi", "google", "tavily", "brave", "duckduckgo", "disabled",
})

# Providers that never need an API key
_FREE_PROVIDERS = frozenset({"duckduckgo"})

# Mapping from mode string → (display_name, handler_fn, required_config_keys)
_PROVIDER_REGISTRY: dict[str, tuple[str, Any, tuple[str, ...]]] = {
    "bing":       ("Bing",       "_search_bing",       ("BING_API_KEY",)),
    "serpapi":    ("SerpAPI",    "_search_serpapi",    ("SERPAPI_API_KEY",)),
    "google":     ("Google CSE", "_search_google_cse",  ("GOOGLE_API_KEY", "GOOGLE_CSE_ID")),
    "tavily":     ("Tavily",     "_search_tavily",     ("TAVILY_API_KEY",)),
    "brave":      ("Brave",      "_search_brave",      ("BRAVE_API_KEY",)),
    "duckduckgo": ("DuckDuckGo", "_search_duckduckgo", ()),
}


def _resolve_search_providers() -> list[tuple[str, Any]]:
    """Resolve the ordered provider list based on WEB_SEARCH_MODE config.

    In *auto* mode (the default) providers are ordered by quality:
    Bing → Tavily → SerpAPI → Brave → Google CSE → DuckDuckGo.

    When *mode* is set to an explicit provider, only that provider is
    attempted (quick-fail if its API key is missing).

    Returns a list of ``(source_label, handler_fn)`` ready to iterate.
    """
    import os as _os

    mode = _os.getenv("WEB_SEARCH_MODE", "auto").strip().lower()
    if mode not in _VALID_SEARCH_MODES:
        mode = "auto"

    # ── disabled ────────────────────────────────────────────────────
    if mode == "disabled":
        return []

    # ── defaults (auto-mode quality order) ───────────────────────────
    auto_order = ("bing", "tavily", "serpapi", "brave", "google", "duckduckgo")

    if mode == "auto":
        ordered = auto_order
    else:
        ordered = (mode,)  # explicit single-provider

    providers: list[tuple[str, Any]] = []
    for key in ordered:
        entry = _PROVIDER_REGISTRY.get(key)
        if entry is None:
            continue
        display_name, _handler_name, required_keys = entry

        # Resolve the handler function at call time to avoid import order issues
        handler_map = {
            "bing": _search_bing,
            "serpapi": _search_serpapi,
            "google": _search_google_cse,
            "tavily": _search_tavily,
            "brave": _search_brave,
            "duckduckgo": _search_duckduckgo,
        }
        handler = handler_map.get(key)
        if handler is None:
            continue

        # Check API key availability
        if required_keys:
            from app import config
            missing = [k for k in required_keys if not getattr(config, k, "")]
            if missing:
                if mode != "auto":
                    logger.debug(
                        "web_search: mode=%s provider %s missing keys: %s",
                        mode, key, missing,
                    )
                continue  # skip this provider

        providers.append((display_name, handler))

    return providers


def _apply_domain_filters(query: str, allowed: list[str] | None, blocked: list[str] | None) -> str:
    """Apply domain filters to the query string via ``site:`` / ``-site:`` syntax.

    This is used by providers that don't natively support domain filtering
    (e.g. Brave, DuckDuckGo).
    """
    clauses: list[str] = []

    if allowed:
        allowed_clause = " OR ".join(f"site:{d.strip()}" for d in allowed if d.strip())
        if allowed_clause:
            clauses.append(f"({allowed_clause})")

    if blocked:
        blocked_clauses = [f"-site:{d.strip()}" for d in blocked if d.strip()]
        clauses.extend(blocked_clauses)

    if not clauses:
        return query

    return f"{' '.join(clauses)} {query}"


async def web_search_handler(
    query: str,
    max_results: int = 5,
    language: str = "zh",
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Search the web using the configured provider mode.

    Provider selection is controlled by the ``WEB_SEARCH_MODE`` env var:
      - ``auto``  (default) → quality-ordered fallback chain
      - ``bing`` / ``tavily`` / ``serpapi`` / ``brave`` / ``google`` / ``duckduckgo``
      - ``disabled`` → returns unavailable message

    Parameters:
        query:              Search keywords.
        max_results:        Max number of results (1-20).
        language:           Language hint (``zh`` / ``en``).
        allowed_domains:    Only include results from these domains.
        blocked_domains:    Exclude results from these domains.
        on_progress:        Optional callback ``(data: dict) -> None`` for
                            streaming progress (``query_update``,
                            ``search_results_received``).

    Returns:
        ``{"success": True, "result": {...}}`` on success (or soft-failure).
    """
    import urllib.parse
    import time as _time_module

    if not query or not query.strip():
        return {"success": False, "error": "搜索关键词不能为空"}

    query = query.strip()
    effective_max = max(1, min(max_results, 20))
    start_time = _time_module.time()

    # ── Resolve provider list based on mode ──────────────────────────
    providers = _resolve_search_providers()

    if not providers:
        # mode = disabled or no API keys at all
        import os as _os_inner
        mode_val = _os_inner.environ.get("WEB_SEARCH_MODE", "auto").strip().lower()
        if mode_val == "disabled":
            reason = "Web search is disabled via WEB_SEARCH_MODE=disabled."
        else:
            reason = "Web search is not configured. Set at least one search API key."
        return _make_unavailable_output(query, start_time, reason)

    # ── Notify progress: search starting ─────────────────────────────
    if on_progress:
        try:
            on_progress({
                "type": "query_update",
                "query": query,
                "provider_count": len(providers),
            })
        # noqa: BLE001 - best-effort (注释已说明故意吞)
        except Exception:
            pass  # never let progress callback break the search

    # ── Iterate providers ────────────────────────────────────────────
    errors: list[str] = []
    for source_name, provider_fn in providers:
        try:
            results = await provider_fn(
                query, effective_max, language,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
            )
            if results:
                duration = _time_module.time() - start_time

                # Notify progress: results received
                if on_progress:
                    try:
                        on_progress({
                            "type": "search_results_received",
                            "query": query,
                            "source": source_name,
                            "result_count": len(results),
                        })
                    # noqa: BLE001 - network tool best-effort, return degraded result
                    except Exception:
                        pass

                return _make_search_output(query, source_name, results, duration)

        except Exception as exc:
            msg = f"{source_name}: {exc}"
            logger.debug("web_search provider %s failed: %s", source_name, exc)
            errors.append(msg)

            # Auth errors should NOT trigger fallback — re-raise as soft-failure
            _err_str = str(exc).lower()
            if any(kw in _err_str for kw in ("401", "403", "unauthorized", "forbidden", "auth")):
                if source_name == providers[-1][0]:
                    break  # last provider, fall through to error reporting

    # ── All providers failed ─────────────────────────────────────────
    duration = _time_module.time() - start_time
    logger.warning("web_search all providers failed for '%s': %s", query, errors)
    encoded = urllib.parse.quote(query)
    return {
        "success": True,  # soft failure — still structured so LLM can respond
        "result": {
            "query": query,
            "source": "search_fallback",
            "duration_seconds": round(duration, 2),
            "results": [{
                "title": f"搜索: {query}",
                "url": f"https://www.google.com/search?q={encoded}",
                "snippet": (
                    f"所有搜索服务暂时不可用。请手动搜索 '{query}' 获取最新信息。"
                    + (f" 错误: {'; '.join(errors[-2:])}" if errors else "")
                ),
            }],
            "total": 1,
        },
    }


def _make_unavailable_output(query: str, start_time: float, reason: str) -> dict[str, Any]:
    """Return a structured "unavailable" response (mode=disabled or no keys)."""
    import time as _time_module
    return {
        "success": True,  # soft failure
        "result": {
            "query": query,
            "source": "disabled",
            "duration_seconds": round(_time_module.time() - start_time, 2),
            "results": [{
                "title": "搜索不可用",
                "url": "",
                "snippet": reason,
            }],
            "total": 1,
        },
    }


def _make_search_output(
    query: str, source: str, results: list[dict], duration_seconds: float,
) -> dict[str, Any]:
    """Build a structured, consistently-formatted search result dict.

    Includes a source-citation reminder for the LLM (mirrors the reference
    implementation's ``mapToolResultToToolResultBlockParam``).
    """
    return {
        "success": True,
        "result": {
            "query": query,
            "source": source,
            "results": results,
            "total": len(results),
            "duration_seconds": round(duration_seconds, 2),
            "note": (
                "请基于以上搜索结果回答用户问题。"
                "在回答中必须使用 markdown 超链接标注信息来源（例如 [标题](URL)）。"
                "若搜索结果与问题无关或为空，请如实告知用户并给出进一步建议。"
            ),
        },
    }


# ── Individual provider implementations ──────────────────────────────
# Each provider receives the same signature:
#   (query, max_results, language, *, allowed_domains, blocked_domains)
# Returns list[dict] on success, raises on failure, returns None if not configured.


async def _search_bing(
    query: str, max_results: int, language: str, *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict] | None:
    """Search via Bing Web Search API v7.

    Requires BING_API_KEY env var.  Free tier: 1 000 calls/month.
    Docs: https://learn.microsoft.com/en-us/bing/search-apis/bing-web-search/
    """
    import httpx
    from app.services.adapter_manager import _get_client

    from app.config import BING_API_KEY

    if not BING_API_KEY:
        return None  # not configured → skip

    url = "https://api.bing.microsoft.com/v7.0/search"
    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    params: dict[str, Any] = {
        "q": query,
        "count": max_results,
        "mkt": "zh-CN" if language == "zh" else "en-US",
        "textFormat": "Raw",
    }

    client = _get_client()
    resp = await client.get(url, headers=headers, params=params, timeout=httpx.Timeout(10.0))
    if resp.status_code >= 400:
        body = resp.text[:300]
        raise Exception(f"HTTP {resp.status_code}: {body}")

    data = resp.json()
    web_pages = data.get("webPages", {}).get("value", [])
    if not web_pages:
        return None

    return [
        {
            "title": page.get("name", ""),
            "url": page.get("url", ""),
            "snippet": (page.get("snippet", ""))[:500],
        }
        for page in web_pages[:max_results]
    ]


async def _search_tavily(
    query: str, max_results: int, language: str, *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict] | None:
    """Search via Tavily Search API.

    Requires TAVILY_API_KEY env var.  Free tier: 1 000 calls/month.
    Tavily is an AI-optimized search API built for RAG and agent workflows.
    Docs: https://docs.tavily.com/
    """
    import httpx
    from app.services.adapter_manager import _get_client

    from app.config import TAVILY_API_KEY

    if not TAVILY_API_KEY:
        return None

    url = "https://api.tavily.com/search"
    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "query": query,
        "max_results": min(max_results, 10),
        "search_depth": "basic",
        "include_answer": False,
    }
    if allowed_domains:
        body["include_domains"] = allowed_domains
    if blocked_domains:
        body["exclude_domains"] = blocked_domains

    client = _get_client()
    resp = await client.post(url, json=body, headers=headers, timeout=httpx.Timeout(15.0))
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    hits = data.get("results", [])
    if not hits:
        return None

    return [
        {
            "title": hit.get("title", ""),
            "url": hit.get("url", ""),
            "snippet": (hit.get("content", ""))[:500],
        }
        for hit in hits[:max_results]
        if isinstance(hit.get("title"), str) and isinstance(hit.get("url"), str)
    ]


async def _search_brave(
    query: str, max_results: int, language: str, *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict] | None:
    """Search via Brave Search API.

    Requires BRAVE_API_KEY env var.  Free tier: 2 000 calls/month.
    Brave doesn't support native domain filtering — we apply ``site:``
    / ``-site:`` syntax to the query instead.
    Docs: https://api.search.brave.com/
    """
    import urllib.parse
    import httpx
    from app.services.adapter_manager import _get_client

    from app.config import BRAVE_API_KEY

    if not BRAVE_API_KEY:
        return None

    # Brave doesn't support per-request language params — domain filters
    # are applied via site: syntax on the query.
    effective_query = _apply_domain_filters(query, allowed_domains, blocked_domains)

    url = "https://api.search.brave.com/res/v1/web/search"
    params: dict[str, Any] = {
        "q": effective_query,
        "count": min(max_results, 20),
    }

    client = _get_client()
    resp = await client.get(url, params=params, headers={
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
    }, timeout=httpx.Timeout(10.0))
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    hits = data.get("web", {}).get("results", [])
    if not hits:
        return None

    return [
        {
            "title": hit.get("title", ""),
            "url": hit.get("url", ""),
            "snippet": (hit.get("description", ""))[:500],
        }
        for hit in hits[:max_results]
    ]


async def _search_serpapi(
    query: str, max_results: int, language: str, *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict] | None:
    """Search via SerpAPI (Google search results as-a-service).

    Requires SERPAPI_API_KEY env var.  Free tier: 100 calls/month.
    Docs: https://serpapi.com/search-api
    """
    import httpx
    from app.services.adapter_manager import _get_client

    from app.config import SERPAPI_API_KEY

    if not SERPAPI_API_KEY:
        return None

    url = "https://serpapi.com/search"
    params: dict[str, Any] = {
        "q": query,
        "api_key": SERPAPI_API_KEY,
        "engine": "google",
        "num": max_results,
        "hl": language,
        "gl": "cn" if language == "zh" else "us",
    }

    client = _get_client()
    resp = await client.get(url, params=params, timeout=httpx.Timeout(12.0))
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    organic = data.get("organic_results", [])
    if not organic:
        # Check for answer_box / knowledge_graph as fallback
        answer = data.get("answer_box") or data.get("knowledge_graph")
        if answer:
            return [{
                "title": answer.get("title", query),
                "url": answer.get("link", ""),
                "snippet": (answer.get("snippet") or answer.get("answer", ""))[:500],
            }]
        return None

    return [
        {
            "title": r.get("title", ""),
            "url": r.get("link", ""),
            "snippet": (r.get("snippet", ""))[:500],
        }
        for r in organic[:max_results]
    ]


async def _search_google_cse(
    query: str, max_results: int, language: str, *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict] | None:
    """Search via Google Custom Search JSON API.

    Requires GOOGLE_API_KEY + GOOGLE_CSE_ID env vars.
    Free tier: 100 calls/day.
    Docs: https://developers.google.com/custom-search/v1/overview
    """
    import httpx
    from app.services.adapter_manager import _get_client

    from app.config import GOOGLE_API_KEY, GOOGLE_CSE_ID

    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        return None

    url = "https://www.googleapis.com/customsearch/v1"
    params: dict[str, Any] = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": min(max_results, 10),  # Google CSE max is 10
        "lr": f"lang_{language}" if language != "zh" else "lang_zh-CN",
    }

    client = _get_client()
    resp = await client.get(url, params=params, timeout=httpx.Timeout(10.0))
    if resp.status_code >= 400:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": (item.get("snippet", ""))[:500],
        }
        for item in items[:max_results]
    ]


async def _search_duckduckgo(
    query: str, max_results: int, language: str, *,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> list[dict] | None:
    """Search via DuckDuckGo.

    Strategy (multi-fallback):
    1. DuckDuckGo Instant Answer JSON API — fast but only handles
       explicit knowledge queries (e.g. "Python", "Einstein birthday").
    2. Bing HTML scraping — zero-key fallback that always produces
       real search results for any query (verified 200 OK).
    """
    import urllib.parse
    import httpx
    from app.services.adapter_manager import _get_client

    # ── Fallback chain: Instant Answer → Bing HTML ──────────────────
    results = await _search_duckduckgo_instant(query, max_results, language)
    if results:
        return results

    results = await _search_bing_html(query, max_results, language)
    if results:
        return results

    return None


async def _search_duckduckgo_instant(
    query: str, max_results: int, language: str,
) -> list[dict] | None:
    """DuckDuckGo Instant Answer JSON API.

    Free, no key, zero rate limits — but only returns structured answers
    for well-known entities/concepts.  A regular search query like
    "AgentHub github" will return 0 hits.
    """
    import urllib.parse
    import httpx
    from app.services.adapter_manager import _get_client

    url = (
        "https://api.duckduckgo.com/?"
        f"q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
    )
    client = _get_client()
    try:
        resp = await client.get(url, headers={"User-Agent": "AgentHub/3.1"}, timeout=httpx.Timeout(8.0))
    except Exception as exc:
        logger.debug("DuckDuckGo Instant network error: %s", exc)
        return None

    if resp.status_code >= 400:
        return None
    if "text/html" in resp.headers.get("content-type", ""):
        return None  # rate-limited

    try:
        data = resp.json()
    # noqa: BLE001 - network tool best-effort, return degraded result
    except Exception:
        return None

    results: list[dict] = []
    if data.get("AbstractText"):
        results.append({
            "title": data.get("AbstractSource", "DuckDuckGo"),
            "url": data.get("AbstractURL", ""),
            "snippet": data["AbstractText"][:500],
        })
    for topic in data.get("RelatedTopics", [])[:max_results - len(results)]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append({
                "title": (topic.get("FirstURL", "").split("/")[-1].replace("_", " ") or topic.get("Text", "")[:60]),
                "url": topic.get("FirstURL", ""),
                "snippet": topic["Text"][:300],
            })
    return results if results else None


async def _search_bing_html(
    query: str, max_results: int, language: str,
) -> list[dict] | None:
    """Bing search via HTML scraping (zero API key required).

    Bing 2024+ uses `<h2><a class="b_algo">` markers for organic results.
    Verified: HTTP 200, 3+ real results for search queries.
    """
    import urllib.parse
    import httpx
    from app.services.adapter_manager import _get_client

    url = (
        "https://www.bing.com/search?"
        f"q={urllib.parse.quote(query)}"
        "&form=QBLH&sp=-1"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    client = _get_client()
    try:
        resp = await client.get(url, headers=headers, timeout=httpx.Timeout(10.0))
    except Exception as exc:
        logger.debug("Bing HTML network error: %s", exc)
        return None

    if resp.status_code != 200:
        return None

    html = resp.text
    if not html or len(html) < 2000:
        return None  # empty or anti-bot

    # Parse: Bing wraps each organic result in <li class="b_algo">
    # containing <h2><a href="...">TITLE</a></h2>
    import re as _re

    # Pattern 1: h2 with embedded a tag (Bing 2024+)
    hits: list[dict] = []
    h2_blocks = _re.findall(r'<h2[^>]*>(.*?)</h2>', html, _re.DOTALL)
    for block in h2_blocks:
        href_m = _re.search(r'href="(https?://[^"]+)"', block)
        title = _re.sub(r'<[^>]+>', '', block).strip()
        if href_m and title and len(title) > 3:
            url_text = href_m.group(1)
            if "bing.com" not in url_text and not url_text.startswith("/"):
                hits.append({"title": title, "url": url_text})

    if not hits:
        # Pattern 2: simpler fallback — any external link with meaningful text
        for m in _re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]{10,})</a>', html):
            url_text = m.group(1)
            title = m.group(2).strip()
            # Accept:
            #   - Direct external URLs
            #   - Bing redirects (bing.com/ck/a?...<real url>) — extract real URL
            if "microsoft.com" in url_text:
                continue  # skip Microsoft noise
            if "bing.com/ck/a" in url_text:
                # Bing redirect — extract the real URL from the 'u' parameter
                real_m = _re.search(r'[?&]u=(https?%3A%2F%2F[^&]+)', url_text)
                if real_m:
                    import urllib.parse as _up
                    url_text = _up.unquote(real_m.group(1))
            hits.append({"title": title, "url": url_text})

    if not hits:
        return None

    # Fetch snippet from nearby <p class="b_lineclamp"> if available
    results: list[dict] = []
    for h in hits[:max_results]:
        snippet_m = _re.search(
            _re.escape(h["url"]).replace(r"\/", r"/")[:40] + r".{0,2000}?class=\"b_lineclamp[^\"]*\"[^>]*>(.*?)</p>",
            html, _re.DOTALL,
        )
        snippet = ""
        if snippet_m:
            snippet = _re.sub(r'<[^>]+>', '', snippet_m.group(1)).strip()[:300]
        results.append({
            "title": h["title"][:100],
            "url": h["url"],
            "snippet": snippet,
        })

    return results if results else None


def normalize_hit(title: Any, url: Any) -> tuple[str, str] | None:
    """Validate and normalize a search hit's title and URL."""
    if not isinstance(title, str) or not isinstance(url, str):
        return None
    title = title.strip()
    url = url.strip()
    if not title or not url:
        return None
    return title, url


# ── file_read ─────────────────────────────────────────────────────────
