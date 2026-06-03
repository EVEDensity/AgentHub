from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import MEMORY_DIR, PROJECT_ROOT
from app.utils.async_file import (
    aexists,
    aisfile,
    aisdir,
    aread_text,
    awrite_text,
    astat_size,
    aiterdir,
    amkdir,
)

logger = logging.getLogger("agenthub.tools.builtin")

# ── Security constraints ──────────────────────────────────────────────
MAX_FILE_READ_BYTES = 1_000_000  # 1 MB
MAX_FILE_LINES = 2000
CODE_EXECUTE_TIMEOUT = 30  # seconds
MAX_CODE_OUTPUT_CHARS = 10_000


def _safe_path(file_path: str, base: Path) -> Path | None:
    """Resolve a path and ensure it stays within the allowed base directory."""
    try:
        resolved = (base / file_path).resolve()
        if not str(resolved).startswith(str(base.resolve())):
            return None
        return resolved
    except (OSError, ValueError):
        return None


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

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(url, headers=headers, params=params)
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.post(url, json=body, headers=headers)
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(url, params=params, headers={
            "Accept": "application/json",
            "X-Subscription-Token": BRAVE_API_KEY,
        })
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0)) as client:
        resp = await client.get(url, params=params)
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

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(url, params=params)
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
    """Search via DuckDuckGo Instant Answer API (free, no key needed).

    This is the ultimate fallback — always available but results quality
    varies.  Rate-limiting may return HTML instead of JSON.
    """
    import urllib.parse
    import httpx

    results: list[dict] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        url = (
            "https://api.duckduckgo.com/?"
            f"q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
        )
        resp = await client.get(url, headers={"User-Agent": "AgentHub/3.1"})

        if resp.status_code >= 400:
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            raise Exception("DuckDuckGo returned HTML (likely rate-limited)")

        try:
            data = resp.json()
        except Exception as json_err:
            raise Exception(f"JSON parse error: {json_err}")

    # Abstract / definition
    if data.get("AbstractText"):
        results.append({
            "title": data.get("AbstractSource", "DuckDuckGo"),
            "url": data.get("AbstractURL", ""),
            "snippet": data["AbstractText"][:500],
        })

    # Related topics
    for topic in data.get("RelatedTopics", [])[:max_results - len(results)]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append({
                "title": (topic.get("FirstURL", "").split("/")[-1].replace("_", " ") or topic.get("Text", "")[:60]),
                "url": topic.get("FirstURL", ""),
                "snippet": topic["Text"][:300],
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

async def file_read_handler(path: str, encoding: str = "utf-8", max_lines: int = 500) -> dict[str, Any]:
    """Read a file from the workspace, returning its content with line numbers."""
    safe = _safe_path(path, PROJECT_ROOT)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围。工作区: {PROJECT_ROOT}"}

    if not await aexists(safe):
        # Try listing similar files as a helpful hint
        parent = safe.parent
        similar: list[str] = []
        try:
            name_lower = safe.name.lower()
            for child in (await aiterdir(parent))[:20]:
                if await aisfile(child) and name_lower[:4] in child.name.lower():
                    similar.append(str(child.relative_to(PROJECT_ROOT)))
        except OSError:
            pass
        hint = f"\n目录 '{parent.relative_to(PROJECT_ROOT)}' 中相似文件: {similar}" if similar else ""
        return {"success": False, "error": f"文件不存在: {path}{hint}"}

    if await aisdir(safe):
        try:
            listing = (await aiterdir(safe))[:50]
            names = [str(p.relative_to(PROJECT_ROOT)) + ("/" if await aisdir(p) else "") for p in listing]
            return {
                "success": True,
                "result": f"目录 '{path}' 内容 ({len(names)} 项):\n" + "\n".join(names),
            }
        except OSError as exc:
            return {"success": False, "error": f"无法列出目录: {exc}"}

    # Check file size
    try:
        size = await astat_size(safe)
        if size > MAX_FILE_READ_BYTES:
            return {
                "success": False,
                "error": f"文件过大 ({size / 1024 / 1024:.1f}MB)。最大允许: {MAX_FILE_READ_BYTES / 1024 / 1024:.0f}MB",
            }
    except OSError as exc:
        return {"success": False, "error": f"无法读取文件信息: {exc}"}

    try:
        content = await aread_text(safe, encoding=encoding)
        lines = content.split("\n")
        total_lines = len(lines)
        truncated = lines[:min(max_lines, len(lines))]
        result_text = "\n".join(truncated)

        if total_lines > max_lines:
            result_text += f"\n\n... [已截断，显示前 {max_lines} 行，共 {total_lines} 行]"

        return {
            "success": True,
            "result": result_text,
            "metadata": {
                "path": str(safe.relative_to(PROJECT_ROOT)),
                "total_lines": total_lines,
                "displayed_lines": len(truncated),
                "size_bytes": size,
                "encoding": encoding,
            },
        }
    except UnicodeDecodeError:
        return {"success": False, "error": f"文件不是有效的 {encoding} 文本文件，可能是二进制文件"}
    except OSError as exc:
        return {"success": False, "error": f"读取文件失败: {exc}"}


# ── file_write ────────────────────────────────────────────────────────

async def file_write_handler(path: str, content: str, mode: str = "overwrite") -> dict[str, Any]:
    """Write content to a file in the workspace.

    Args:
        path: Relative path within PROJECT_ROOT.
        content: The text content to write.
        mode: "overwrite" (default) replaces the file; "append" adds to the end.
    """
    safe = _safe_path(path, PROJECT_ROOT)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if await aexists(safe) and await aisdir(safe):
        return {"success": False, "error": f"'{path}' 是一个目录，无法写入"}

    try:
        # Ensure parent directory exists
        await amkdir(safe.parent)

        if mode == "append" and await aexists(safe):
            existing = await aread_text(safe, encoding="utf-8")
            await awrite_text(safe, existing + "\n" + content, encoding="utf-8")
            action = "追加"
        else:
            await awrite_text(safe, content, encoding="utf-8")
            action = "覆写"

        size = await astat_size(safe)
        return {
            "success": True,
            "result": f"文件 '{path}' {action}成功 ({size} 字节)",
            "metadata": {
                "path": str(safe.relative_to(PROJECT_ROOT)),
                "size_bytes": size,
                "mode": mode,
            },
        }
    except OSError as exc:
        return {"success": False, "error": f"写入文件失败: {exc}"}


# ── code_execute ──────────────────────────────────────────────────────

async def code_execute_handler(code: str, language: str = "python", timeout: int = 30) -> dict[str, Any]:
    """Execute code in a sandboxed subprocess.

    Currently supports: python, bash
    Security: runs in a temp directory with limited permissions.
    """
    if not code or not code.strip():
        return {"success": False, "error": "代码内容不能为空"}

    if language not in ("python", "bash", "sh"):
        return {"success": False, "error": f"不支持的语言: {language}。支持: python, bash"}

    # Apply timeout cap
    effective_timeout = min(timeout, CODE_EXECUTE_TIMEOUT)

    try:
        with tempfile.TemporaryDirectory(prefix="agenthub_exec_") as tmpdir:
            if language == "python":
                script_path = Path(tmpdir) / "script.py"
                await awrite_text(script_path, code, encoding="utf-8")
                cmd = ["python", str(script_path)]
            else:
                script_path = Path(tmpdir) / "script.sh"
                await awrite_text(script_path, code, encoding="utf-8")
                cmd = ["bash", str(script_path)]

            proc = await _run_subprocess(cmd, effective_timeout, cwd=tmpdir)

            stdout = proc.get("stdout", "")[:MAX_CODE_OUTPUT_CHARS]
            stderr = proc.get("stderr", "")[:MAX_CODE_OUTPUT_CHARS]
            exit_code = proc.get("exit_code", -1)

            result_parts: list[str] = []
            if stdout:
                result_parts.append(f"[标准输出]\n{stdout}")
            if stderr:
                result_parts.append(f"[标准错误]\n{stderr}")
            if exit_code != 0:
                result_parts.append(f"[退出码: {exit_code}]")
            if not result_parts:
                result_parts.append("[无输出]")

            return {
                "success": exit_code == 0,
                "result": "\n\n".join(result_parts),
                "metadata": {
                    "language": language,
                    "exit_code": exit_code,
                    "stdout_length": len(stdout),
                    "stderr_length": len(stderr),
                    "timeout_seconds": effective_timeout,
                },
            }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"代码执行超时 ({effective_timeout}秒)",
            "metadata": {"language": language, "timeout_seconds": effective_timeout},
        }
    except Exception as exc:
        logger.exception("code_execute failed")
        return {"success": False, "error": f"代码执行异常: {exc}"}


async def _run_subprocess(cmd: list[str], timeout: int, cwd: str) -> dict[str, Any]:
    """Run a subprocess with timeout and return stdout/stderr/exit_code."""
    try:
        proc = await __import__("asyncio").create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await __import__("asyncio").wait_for(
            proc.communicate(), timeout=timeout
        )
        return {
            "stdout": stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else "",
            "stderr": stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else "",
            "exit_code": proc.returncode if proc.returncode is not None else -1,
        }
    except __import__("asyncio").TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        raise subprocess.TimeoutExpired(cmd, timeout)


# ── memory_search ─────────────────────────────────────────────────────

async def memory_search_handler(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the agent's persistent memory for relevant information."""
    if not query or not query.strip():
        return {"success": False, "error": "搜索关键词不能为空"}

    query = query.strip()
    try:
        from app.services.memory.storage import MemoryStorage
        from app.services.memory.models import MemoryType

        storage = MemoryStorage(MEMORY_DIR)
        headers = await storage.list_headers(max_files=200)

        # Simple keyword matching with scoring
        scored: list[tuple[float, dict]] = []
        query_lower = query.lower()
        for h in headers:
            score = 0.0
            name_lower = (h.name or "").lower()
            desc_lower = (h.description or "").lower()
            type_str = h.type.value if isinstance(h.type, MemoryType) else str(h.type)

            # Exact name match
            if query_lower in name_lower:
                score += 10
            # Description match
            if query_lower in desc_lower:
                score += 5
            # Type match
            if query_lower in type_str.lower():
                score += 3
            # Word-level matching
            query_words = set(query_lower.split())
            name_words = set(name_lower.replace("_", " ").replace("-", " ").split())
            desc_words = set(desc_lower.split())
            score += len(query_words & name_words) * 2
            score += len(query_words & desc_words) * 1

            if score > 0:
                scored.append((score, {
                    "name": h.name,
                    "filename": h.filename,
                    "type": type_str,
                    "description": h.description[:200],
                    "relevance_score": round(score, 1),
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:max_results]]

        if results:
            return {
                "success": True,
                "result": {
                    "query": query,
                    "results": results,
                    "total": len(results),
                    "searched_files": len(headers),
                },
            }
        else:
            return {
                "success": True,
                "result": {
                    "query": query,
                    "results": [],
                    "total": 0,
                    "message": f"在 {len(headers)} 条记忆中未找到与 '{query}' 相关的内容",
                },
            }
    except Exception as exc:
        logger.exception("memory_search failed")
        return {"success": False, "error": f"记忆搜索失败: {exc}"}
