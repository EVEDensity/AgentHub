from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import MEMORY_DIR
from app.services.tools.sandbox_executor import sandbox_executor
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
CODE_EXECUTE_TIMEOUT = 30  # seconds (script execution)
CODE_EXECUTE_INSTALL_TIMEOUT = 120  # seconds (pip/npm install)
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


# ── Diff helper (used by file_write / file_patch for broadcast) ────────────

def _compute_unified_diff(old_text: str, new_text: str, path: str = "") -> str:
    """Compute a unified diff between two text strings.  Returns empty string
    when the texts are identical or difflib is unavailable."""
    if old_text == new_text:
        return ""
    try:
        import difflib
        diff_lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=path or "a/file",
                tofile=path or "b/file",
                lineterm="",
            )
        )
        return "".join(diff_lines[:100])  # cap at 100 lines for broadcast
    except Exception:
        return ""


# ── Fire-and-forget helpers (must NEVER block the tool response) ───────────

async def _broadcast_workspace_change(
    session_id: str, path: str, operation: str, size_bytes: int,
    diff_preview: str = "", old_path: str = "", user_id: str = "",
) -> None:
    """Broadcast a workspace_change event after a successful file op."""
    try:
        from app.services.websocket_manager import manager
        if session_id:
            await manager.broadcast_workspace_change(
                session_id=session_id, path=path, operation=operation,
                user_id=user_id, size_bytes=size_bytes,
                diff_preview=diff_preview, old_path=old_path,
            )
    except Exception:
        pass  # broadcast failure must never block the tool


async def _record_file_version(
    path: str, content: str, session_id: str = "", user_id: str = "",
) -> str:
    """Record a file version and return the SHA-256 hash."""
    try:
        from app.services.file_version_tracker import file_version_tracker
        sid = session_id or _get_sid_fast()
        uid = user_id or _get_uid_fast()
        fv = file_version_tracker.record_write(sid, path, content, uid)
        return fv.sha256
    except Exception:
        return ""


async def _auto_git_commit(path: str, user_id: str, operation: str) -> None:
    """Auto-commit to git after a file write (fire-and-forget)."""
    try:
        from app.config import AGENTHUB_FILE_AUTO_GIT
        if not AGENTHUB_FILE_AUTO_GIT:
            return
        import asyncio as _asyncio
        from app.services.git_service import git_service
        await _asyncio.to_thread(git_service.auto_commit, path, user_id, operation)
    except Exception:
        pass


def _get_sid_fast() -> str:
    try:
        from app.services.workspace_context import get_workspace_session_id
        return get_workspace_session_id()
    except Exception:
        return ""


def _get_uid_fast() -> str:
    try:
        from app.services.workspace_context import get_workspace_user_id
        return get_workspace_user_id()
    except Exception:
        return ""


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
    """Search via DuckDuckGo Instant Answer API (free, no key needed).

    This is the ultimate fallback — always available but results quality
    varies.  Rate-limiting may return HTML instead of JSON.
    """
    import urllib.parse
    import httpx
    from app.services.adapter_manager import _get_client

    results: list[dict] = []

    url = (
        "https://api.duckduckgo.com/?"
        f"q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
    )
    client = _get_client()
    resp = await client.get(url, headers={"User-Agent": "AgentHub/3.1"}, timeout=httpx.Timeout(8.0))

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
    """Read a file from the user's per-session workspace."""
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if not await aexists(safe):
        parent = safe.parent
        similar: list[str] = []
        try:
            name_lower = safe.name.lower()
            for child in (await aiterdir(parent))[:20]:
                if await aisfile(child) and name_lower[:4] in child.name.lower():
                    similar.append(str(child.relative_to(ws_root)))
        except OSError:
            pass
        hint = f"\n目录 '{parent.relative_to(ws_root)}' 中相似文件: {similar}" if similar else ""
        return {"success": False, "error": f"文件不存在: {path}{hint}"}

    if await aisdir(safe):
        try:
            listing = (await aiterdir(safe))[:50]
            names = [str(p.relative_to(ws_root)) + ("/" if await aisdir(p) else "") for p in listing]
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

        # ── Record version hash for conflict detection ──────────────────
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            sid = _get_sid_fast()
            uid = _get_uid_fast()
            fv = file_version_tracker.record_read(sid, path, content, uid)
            sha256_hash = fv.sha256[:12]
        except Exception:
            pass

        return {
            "success": True,
            "result": result_text,
            "metadata": {
                "path": str(safe.relative_to(ws_root)),
                "total_lines": total_lines,
                "displayed_lines": len(truncated),
                "size_bytes": size,
                "encoding": encoding,
                "sha256": sha256_hash,
            },
        }
    except UnicodeDecodeError:
        return {"success": False, "error": f"文件不是有效的 {encoding} 文本文件，可能是二进制文件"}
    except OSError as exc:
        return {"success": False, "error": f"读取文件失败: {exc}"}


# ── file_write ────────────────────────────────────────────────────────

async def file_write_handler(
    path: str,
    content: str,
    mode: str = "overwrite",
    expected_sha256: str = "",
) -> dict[str, Any]:
    """Write content to a file in the user's per-session workspace.

    Args:
        path: Relative path within the session workspace.
        content: The text content to write.
        mode: "overwrite" (default) replaces the file; "append" adds to the end.
        expected_sha256: Optional hash from a prior ``file_read`` call.
            When provided, conflict detection compares it against the
            tracked version and warns if another user modified the file
            in the meantime.
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if await aexists(safe) and await aisdir(safe):
        return {"success": False, "error": f"'{path}' 是一个目录，无法写入"}

    # ── Context ─────────────────────────────────────────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    # ── Pre-read original content (for diff + conflict + backup) ────────
    original_text = ""
    if mode == "overwrite" and await aexists(safe):
        try:
            original_text = await aread_text(safe, encoding="utf-8")
        except UnicodeDecodeError:
            original_text = ""

    # ── Conflict detection ──────────────────────────────────────────────
    conflict_warning = ""
    if expected_sha256 and original_text:
        try:
            from app.services.file_version_tracker import file_version_tracker
            check = file_version_tracker.check_conflict(sid, path, expected_sha256)
            if check["conflict"]:
                cv = check["current_version"]
                conflict_user = (cv.written_by_user or cv.written_by_agent or "其他用户")
                conflict_warning = (
                    f"⚠️ 冲突检测: 文件被 {conflict_user} 修改过。"
                )
                # Backup the current version so nothing is lost
                backup_path = safe.with_suffix(safe.suffix + ".conflict_backup")
                try:
                    await awrite_text(backup_path, original_text, encoding="utf-8")
                    conflict_warning += f" 原文件已备份为 {backup_path.name}。"
                except OSError:
                    pass
                # Broadcast conflict event
                try:
                    from app.services.websocket_manager import manager
                    diff_preview = _compute_unified_diff(original_text, content, path)
                    await manager.broadcast_file_conflict(
                        session_id=sid, path=path,
                        ours_user_id=uid or "",
                        theirs_user_id=cv.written_by_user,
                        ours_preview=content[:1000],
                        theirs_preview=original_text[:1000],
                        diff=diff_preview,
                        backup_path=backup_path.name if backup_path.exists() else "",
                    )
                except Exception:
                    pass
        except Exception:
            pass  # conflict detection is advisory — never block the write

    # ── Advisory locking ────────────────────────────────────────────────
    lock_acquired = False
    try:
        from app.services.file_lock import file_lock_manager
        lock_result = file_lock_manager.acquire(sid, path, uid)
        lock_acquired = lock_result["ok"]
        if not lock_result["ok"]:
            existing_lock = lock_result["lock"]
            if not conflict_warning:
                conflict_warning = ""
            conflict_warning += (
                f" 🔒 文件被 {existing_lock.holder_name or existing_lock.holder_user_id} 锁定"
                f"（{existing_lock.remaining_seconds:.0f}秒后过期）。"
            )
    except Exception:
        pass

    # ── Write ───────────────────────────────────────────────────────────
    try:
        await amkdir(safe.parent)

        if mode == "append" and original_text:
            new_full = original_text + "\n" + content
            await awrite_text(safe, new_full, encoding="utf-8")
            action = "追加"
        else:
            await awrite_text(safe, content, encoding="utf-8")
            action = "覆写"

        size = await astat_size(safe)

        # ── Post-write: track version ───────────────────────────────────
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            fv = file_version_tracker.record_write(
                sid, path,
                content if mode == "overwrite" else (original_text + "\n" + content),
                uid, "",
            )
            sha256_hash = fv.sha256
        except Exception:
            pass

        # ── Post-write: broadcast workspace_change ──────────────────────
        diff_preview = ""
        if mode == "overwrite" and original_text:
            diff_preview = _compute_unified_diff(original_text, content, path)
        # Fire-and-forget — don't await, don't block
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, diff_preview, user_id=uid)
        )

        # ── Post-write: auto git commit ─────────────────────────────────
        _asyncio.ensure_future(_auto_git_commit(path, uid, action))

        # ── Build result ────────────────────────────────────────────────
        result_msg = f"文件 '{path}' {action}成功 ({size} 字节)"
        if conflict_warning:
            result_msg = conflict_warning + "\n" + result_msg

        metadata: dict[str, Any] = {
            "path": str(safe.relative_to(ws_root)),
            "size_bytes": size,
            "mode": mode,
            "sha256": sha256_hash[:12] if sha256_hash else "",
        }
        if conflict_warning:
            metadata["conflict"] = True
        if lock_acquired:
            metadata["lock_held"] = True

        return {"success": True, "result": result_msg, "metadata": metadata}
    except OSError as exc:
        return {"success": False, "error": f"写入文件失败: {exc}"}
    finally:
        # ── Release lock ────────────────────────────────────────────────
        if lock_acquired:
            try:
                from app.services.file_lock import file_lock_manager
                file_lock_manager.release(sid, path, uid)
            except Exception:
                pass


# ── file_write_batch ──────────────────────────────────────────────────

async def file_write_batch_handler(
    paths_contents: list[dict[str, str]],
) -> dict[str, Any]:
    """Write multiple files to the workspace in a single call.

    Creates parent directories automatically (acts as mkdir -p for each
    file's parent path).  Each item in *paths_contents* must have:

        - ``path`` (str, required) — relative path within the workspace
        - ``content`` (str, required) — text content to write

    Example::

        [
            {"path": "src/main.py", "content": "print('hello')"},
            {"path": "src/utils/helpers.py", "content": "def add(a,b): return a+b"},
        ]
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not isinstance(paths_contents, list) or len(paths_contents) == 0:
        return {"success": False, "error": "paths_contents 必须是非空数组，每项包含 path 和 content 字段"}

    ws_root = get_workspace_root()
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    results: list[dict] = []
    created_dirs: set[str] = set()

    for i, item in enumerate(paths_contents):
        if not isinstance(item, dict):
            results.append({"index": i, "success": False, "error": "数组项必须是对象 {path, content}"})
            continue

        path = item.get("path", "")
        content = item.get("content", "")
        if not path:
            results.append({"index": i, "success": False, "error": "缺少必填字段 path"})
            continue
        if not isinstance(content, str):
            results.append({"index": i, "success": False, "error": "content 必须是字符串"})
            continue

        safe = resolve_workspace_path(path)
        if safe is None:
            results.append({"index": i, "path": path, "success": False, "error": f"路径 '{path}' 超出工作区允许范围"})
            continue

        if await aexists(safe) and await aisdir(safe):
            results.append({"index": i, "path": path, "success": False, "error": f"'{path}' 是一个目录，无法作为文件写入"})
            continue

        # ── Auto-create parent directories (folder creation) ──────────
        parent = safe.parent
        parent_rel = str(parent.relative_to(ws_root))
        try:
            if not await aexists(parent):
                await amkdir(parent)
                if parent_rel not in created_dirs:
                    created_dirs.add(parent_rel)
        except OSError as exc:
            results.append({"index": i, "path": path, "success": False, "error": f"无法创建目录 '{parent_rel}': {exc}"})
            continue

        # ── Pre-read original for diff (if overwriting) ───────────────
        original_text = ""
        if await aexists(safe):
            try:
                original_text = await aread_text(safe, encoding="utf-8")
            except UnicodeDecodeError:
                original_text = ""

        # ── Write ─────────────────────────────────────────────────────
        try:
            await awrite_text(safe, content, encoding="utf-8")
            size = await astat_size(safe)
        except OSError as exc:
            results.append({"index": i, "path": path, "success": False, "error": f"写入失败: {exc}"})
            continue

        # ── Post-write: track version ─────────────────────────────────
        try:
            from app.services.file_version_tracker import file_version_tracker
            file_version_tracker.record_write(sid, path, content, uid, "")
        except Exception:
            pass

        # ── Post-write: broadcast + git (fire-and-forget) ────────────
        diff_preview = ""
        if original_text:
            diff_preview = _compute_unified_diff(original_text, content, path)
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, diff_preview, user_id=uid)
        )
        _asyncio.ensure_future(_auto_git_commit(path, uid, "batch_write"))

        results.append({
            "index": i,
            "path": path,
            "success": True,
            "result": f"'{path}' 写入成功 ({size} 字节)",
            "metadata": {"path": path, "size_bytes": size},
        })

    # ── Summary ───────────────────────────────────────────────────────
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count

    summary_parts: list[str] = [
        f"批量写入完成: {success_count}/{len(results)} 成功",
    ]
    if fail_count > 0:
        failed_paths = [r.get("path", f"index {r.get('index')}") for r in results if not r.get("success")]
        summary_parts.append(f"，{fail_count} 失败: {', '.join(failed_paths[:5])}")
    if created_dirs:
        summary_parts.append(f"。自动创建目录: {', '.join(sorted(created_dirs)[:10])}")

    return {
        "success": fail_count == 0,
        "result": "".join(summary_parts),
        "metadata": {
            "total": len(results),
            "success_count": success_count,
            "fail_count": fail_count,
            "created_dirs": sorted(created_dirs),
            "files": results,
        },
    }


# ── code_execute ──────────────────────────────────────────────────────

async def code_execute_handler(
    code: str,
    language: str = "python",
    timeout: int = 30,
    cwd: str = ".",
) -> dict[str, Any]:
    """Execute code in a sandboxed subprocess within the workspace.

    The working directory is the user's per-session workspace (or a
    subdirectory within it), so scripts can access, import, and test
    files the agent has written.  Unlike the old implementation that
    ran in a throw-away temp directory, this gives the agent a genuine
    write→execute→debug loop.

    Supports: python, bash
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not code or not code.strip():
        return {"success": False, "error": "代码内容不能为空"}

    lang = language.lower()
    if lang in ("sh", "shell"):
        lang = "bash"
    if lang not in ("python", "bash"):
        return {"success": False, "error": f"不支持的语言: {language}。支持: python, bash"}

    # ── Remote sandbox execution (P0.1-B) ──────────────────────────────
    # When SANDBOX_MODE is "remote" or "auto", try the Go sandbox-service
    # first. Remote mode runs code in an isolated Docker container without
    # workspace access. Auto mode falls back to subprocess on failure.
    if sandbox_executor.mode in ("remote", "auto"):
        try:
            remote_result = await sandbox_executor._execute_remote(
                code, lang, min(timeout, CODE_EXECUTE_TIMEOUT)
            )
            stdout = sandbox_executor.sanitize_output(remote_result.stdout)[:MAX_CODE_OUTPUT_CHARS]
            stderr = sandbox_executor.sanitize_output(remote_result.stderr)[:MAX_CODE_OUTPUT_CHARS]

            result_parts: list[str] = []
            if stdout:
                result_parts.append(f"[标准输出]\n{stdout}")
            if stderr:
                result_parts.append(f"[标准错误]\n{stderr}")
            if remote_result.exit_code != 0:
                result_parts.append(f"[退出码: {remote_result.exit_code}]")
            if not result_parts:
                result_parts.append("[无输出]")

            return {
                "success": remote_result.success,
                "result": "\n\n".join(result_parts),
                "metadata": {
                    "language": lang,
                    "exit_code": remote_result.exit_code,
                    "stdout_length": len(stdout),
                    "stderr_length": len(stderr),
                    "duration_ms": remote_result.duration_ms,
                    "sandbox_mode": "remote",
                },
            }
        except Exception as exc:
            if sandbox_executor.mode == "remote":
                return {"success": False, "error": f"远程沙盒执行失败: {exc}"}
            # auto: fall through to subprocess
            logger.warning("code_execute: remote sandbox failed, using subprocess: %s", exc)

    # ── Resolve working directory ─────────────────────────────────────
    ws_root = get_workspace_root()
    if cwd and cwd.strip() and cwd.strip() != ".":
        safe_cwd = resolve_workspace_path(cwd.strip())
        if safe_cwd is None:
            return {"success": False, "error": f"工作目录 '{cwd}' 超出工作区允许范围"}
        if not safe_cwd.exists():
            safe_cwd.mkdir(parents=True, exist_ok=True)
        work_dir = str(safe_cwd)
        work_dir_rel = str(safe_cwd.relative_to(ws_root))
    else:
        work_dir = str(ws_root)
        work_dir_rel = "."

    # ── Detect install commands → use longer timeout ─────────────────
    code_stripped = code.strip()
    is_install_cmd = _is_install_command(code_stripped, lang)
    effective_timeout = min(
        timeout,
        CODE_EXECUTE_INSTALL_TIMEOUT if is_install_cmd else CODE_EXECUTE_TIMEOUT,
    )

    # ── Create .agenthub_exec/ scratch dir inside workspace ───────────
    # Scripts are written here (not system /tmp) so they sit alongside
    # workspace files and can import sibling modules naturally.
    exec_dir = ws_root / ".agenthub_exec"
    try:
        exec_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        exec_dir = Path(tempfile.mkdtemp(prefix="agenthub_exec_"))

    try:
        if lang == "python":
            # ── Python execution ────────────────────────────────────
            script_path = exec_dir / "script.py"
            await awrite_text(script_path, code, encoding="utf-8")
            cmd = _build_python_cmd(ws_root, script_path)
        else:
            # ── Bash execution ──────────────────────────────────────
            if _is_one_liner(code_stripped):
                # Single command (e.g. "pip install flask", "npm install"):
                # run directly via bash -c in the workspace.
                script_path = None
                cmd = ["bash", "-lc", code_stripped]
            else:
                script_path = exec_dir / "script.sh"
                await awrite_text(script_path, code, encoding="utf-8")
                cmd = ["bash", str(script_path)]

        proc = await _run_subprocess(cmd, effective_timeout, cwd=work_dir)

        stdout = sandbox_executor.sanitize_output(proc.get("stdout", ""))[:MAX_CODE_OUTPUT_CHARS]
        stderr = sandbox_executor.sanitize_output(proc.get("stderr", ""))[:MAX_CODE_OUTPUT_CHARS]
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

        metadata: dict[str, Any] = {
            "language": lang,
            "exit_code": exit_code,
            "stdout_length": len(stdout),
            "stderr_length": len(stderr),
            "timeout_seconds": effective_timeout,
            "cwd": work_dir_rel,
            "is_install": is_install_cmd,
        }

        return {
            "success": exit_code == 0,
            "result": "\n\n".join(result_parts),
            "metadata": metadata,
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"代码执行超时 ({effective_timeout}秒){' (安装命令)' if is_install_cmd else ''}",
            "metadata": {
                "language": lang,
                "timeout_seconds": effective_timeout,
                "cwd": work_dir_rel,
                "is_install": is_install_cmd,
            },
        }
    except Exception as exc:
        logger.exception("code_execute failed")
        return {"success": False, "error": f"代码执行异常: {exc}"}
    finally:
        # Clean up the script file (leave exec_dir for future runs)
        if lang == "python" and script_path:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass
        elif lang == "bash" and script_path:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass


def _is_install_command(code: str, lang: str) -> bool:
    """Detect whether *code* is a dependency installation command."""
    first_line = code.split("\n")[0].strip().lower()
    install_prefixes = (
        "pip install", "pip3 install", "python -m pip install",
        "npm install", "npm i ", "npm ci",
        "yarn add", "yarn install",
        "pnpm install", "pnpm add",
        "poetry install", "poetry add",
        "conda install",
        "gem install",
        "cargo install", "cargo add",
        "go get", "go install",
    )
    return any(first_line.startswith(p) for p in install_prefixes)


def _is_one_liner(code: str) -> bool:
    """Heuristic: is *code* a single shell command (not a multi-line script)?"""
    lines = [l for l in code.split("\n") if l.strip() and not l.strip().startswith("#")]
    if len(lines) > 1:
        return False
    # If there are no common script markers (shebang, function, if/while/for),
    # treat it as a single command.
    script_keywords = ("#!/", "function ", "if ", "while ", "for ", "case ", "do ", "then ")
    combined = "\n".join(lines)
    return not any(combined.strip().startswith(kw) for kw in script_keywords)


def _build_python_cmd(ws_root: Path, script_path: Path) -> list[str]:
    """Build the python command, auto-activating workspace venv if present."""
    # Check for workspace .venv
    venv_python = ws_root / ".venv" / "Scripts" / "python.exe"  # Windows
    if not venv_python.exists():
        venv_python = ws_root / ".venv" / "bin" / "python"       # Unix
    if venv_python.exists():
        return [str(venv_python), str(script_path)]
    return ["python", str(script_path)]


async def _run_subprocess(cmd: list[str], timeout: int, cwd: str) -> dict[str, Any]:
    """Run a subprocess with timeout and return stdout/stderr/exit_code."""
    proc = None
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
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        raise subprocess.TimeoutExpired(cmd, timeout)


# ── file_search ────────────────────────────────────────────────────────

async def file_search_handler(
    pattern: str,
    path: str = ".",
    glob: str = "*",
    max_results: int = 30,
    context_lines: int = 2,
    ignore_case: bool = True,
) -> dict[str, Any]:
    """Search file contents using regex pattern (grep-like).

    Walks the workspace directory tree, filters files by glob pattern,
    and searches each file's content for matches.  Returns matching lines
    with file path, line number, and surrounding context.

    Args:
        pattern: Regex pattern to search for.
        path: Relative directory path to search (default: workspace root).
        glob: File glob filter (e.g. ``*.py``, ``*.{ts,tsx}``).
        max_results: Maximum number of matches to return.
        context_lines: Lines of context before/after each match.
        ignore_case: Case-insensitive matching (default True).
    """
    import re as _re
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not pattern or not pattern.strip():
        return {"success": False, "error": "搜索模式不能为空"}

    pattern = pattern.strip()
    ws_root = get_workspace_root()
    search_path = resolve_workspace_path(path)
    if search_path is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if not await aisdir(search_path):
        return {"success": False, "error": f"目录不存在: {path}"}

    # Build glob pattern
    import fnmatch as _fnmatch
    glob_parts = [g.strip() for g in glob.split(",") if g.strip()]

    # Compile regex
    try:
        flags = _re.IGNORECASE if ignore_case else 0
        regex = _re.compile(pattern, flags)
    except _re.error as exc:
        return {"success": False, "error": f"正则表达式无效: {exc}"}

    matches: list[dict[str, Any]] = []
    scanned_files = 0
    max_files = 200  # safety limit

    try:
        for root_dir, dirs, files in _walk_sync(search_path):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for fname in files:
                if fname.startswith("."):
                    continue
                if max_files <= 0:
                    break
                max_files -= 1

                # Glob filter
                if glob_parts and not any(_fnmatch.fnmatch(fname, gp) for gp in glob_parts):
                    continue

                file_path = Path(root_dir) / fname
                # Skip binary/large files
                try:
                    size = file_path.stat().st_size
                    if size > 500_000:  # 500KB limit
                        continue
                except OSError:
                    continue

                scanned_files += 1
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                lines = content.split("\n")
                for i, line in enumerate(lines):
                    if regex.search(line):
                        ctx_start = max(0, i - context_lines)
                        ctx_end = min(len(lines), i + context_lines + 1)
                        context_block = "\n".join(
                            f"{j+1}: {lines[j]}" for j in range(ctx_start, ctx_end)
                        )
                        matches.append({
                            "file": str(file_path.relative_to(ws_root)),
                            "line": i + 1,
                            "match": line.strip()[:200],
                            "context": context_block[:800],
                        })
                        if len(matches) >= max_results:
                            break
                if len(matches) >= max_results:
                    break
            if max_files <= 0 or len(matches) >= max_results:
                break
    except Exception as exc:
        logger.exception("file_search walk failed")
        return {"success": False, "error": f"搜索文件失败: {exc}"}

    return {
        "success": True,
        "result": {
            "pattern": pattern,
            "path": str(search_path.relative_to(ws_root)),
            "matches": matches,
            "total_matches": len(matches),
            "scanned_files": scanned_files,
        },
    }


def _walk_sync(root: Path) -> Any:
    """Synchronous walk wrapper — simple implementation."""
    import os as _os
    for dirpath_str, dirnames, filenames in _os.walk(str(root)):
        dirpath = Path(dirpath_str)
        yield dirpath, dirnames, filenames


# ── file_patch ──────────────────────────────────────────────────────────

async def file_patch_handler(
    path: str,
    diff: str,
) -> dict[str, Any]:
    """Apply a unified diff patch to a file.

    Supports the standard unified diff format (output from ``diff -u``,
    ``git diff``, etc.).  Each hunk header ``@@ -a,n +b,m @@`` is parsed
    and applied to the target file.

    Args:
        path: Relative path to the file to patch (within workspace).
        diff: Unified diff text (one or more hunks).

    Returns:
        Result with patched content preview and change summary.
    """
    import re as _re
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not path or not path.strip():
        return {"success": False, "error": "文件路径不能为空"}
    if not diff or not diff.strip():
        return {"success": False, "error": "diff 内容不能为空"}

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    # Read original file
    if not await aexists(safe):
        return {"success": False, "error": f"文件不存在: {path}"}

    try:
        original = await aread_text(safe, encoding="utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": f"文件不是有效的 UTF-8 文本文件"}

    original_lines = original.split("\n")

    # Parse diff hunks
    hunk_pattern = _re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
    hunks: list[dict] = []
    current_hunk = None
    lines_consumed = 0

    for line in diff.split("\n"):
        m = hunk_pattern.match(line)
        if m:
            if current_hunk:
                hunks.append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            current_hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "context": m.group(5).strip(),
                "edits": [],
            }
            lines_consumed = 0
        elif current_hunk is not None:
            if line.startswith(" ") or line == "" or (not line.startswith(("+", "-"))):
                # Context line
                current_hunk["edits"].append(("context", line[1:] if line.startswith(" ") else line))
                lines_consumed += 1
            elif line.startswith("-"):
                current_hunk["edits"].append(("remove", line[1:]))
            elif line.startswith("+"):
                current_hunk["edits"].append(("add", line[1:]))
            # Skip other lines (e.g. "\ No newline at end of file")

    if current_hunk:
        hunks.append(current_hunk)

    if not hunks:
        return {"success": False, "error": "无法解析 diff，未找到有效的 hunk 头（@@ 行）"}

    # Apply hunks (in reverse order to preserve line numbers)
    result_lines = list(original_lines)
    added = 0
    removed = 0

    for hunk in reversed(hunks):
        old_start = hunk["old_start"] - 1  # 0-indexed

        # Calculate how many old lines this hunk covers
        old_line_count = sum(1 for e in hunk["edits"] if e[0] in ("context", "remove"))

        if old_start > len(result_lines):
            continue

        # Build replacement lines
        replacement: list[str] = []
        for action, text in hunk["edits"]:
            if action in ("context", "add"):
                replacement.append(text)
                if action == "add":
                    added += 1
            # "remove" lines are skipped
            if action == "remove":
                removed += 1

        # Replace the hunk segment
        result_lines[old_start:old_start + old_line_count] = replacement

    patched = "\n".join(result_lines)

    # ── Write the patched file ──────────────────────────────────────────
    try:
        await awrite_text(safe, patched, encoding="utf-8")
    except OSError as exc:
        return {"success": False, "error": f"写入补丁文件失败: {exc}"}

    # ── Post-patch: track version, broadcast, git ──────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    # Record version
    sha256_hash = ""
    try:
        from app.services.file_version_tracker import file_version_tracker
        fv = file_version_tracker.record_write(sid, path, patched, uid, "")
        sha256_hash = fv.sha256
    except Exception:
        pass

    # Broadcast
    size = len(patched.encode("utf-8"))
    import asyncio as _asyncio
    _asyncio.ensure_future(
        _broadcast_workspace_change(sid, path, "write", size, diff, user_id=uid)
    )
    _asyncio.ensure_future(_auto_git_commit(path, uid, "patch"))

    # Preview
    preview = patched[:2000]
    if len(patched) > 2000:
        preview += "\n\n... [已截断]"

    return {
        "success": True,
        "result": f"补丁应用成功。{added} 行新增，{removed} 行删除。\n\n[文件预览]\n{preview}",
        "metadata": {
            "path": str(safe.relative_to(ws_root)),
            "lines_added": added,
            "lines_removed": removed,
            "total_lines": len(result_lines),
            "total_chars": len(patched),
            "sha256": sha256_hash[:12] if sha256_hash else "",
        },
    }


# ── memory_save ─────────────────────────────────────────────────────────

async def memory_save_handler(
    name: str,
    content: str,
    type: str = "reference",
    description: str = "",
) -> dict[str, Any]:
    """Save a persistent memory entry for the current user.

    Memories persist across sessions and are searchable via ``memory_search``.
    Uses the file-based memory storage system (MEMORY.md index + .md files).

    Args:
        name: Short kebab-case slug for the memory (e.g. ``user-preferences``).
        content: The memory content (markdown body).
        type: Memory type — ``user`` | ``feedback`` | ``project`` | ``reference``.
        description: One-line summary for the MEMORY.md index.
    """
    from app.config import MEMORY_DIR
    from app.services.memory.storage import MemoryStorage
    from app.services.memory.models import MemoryType

    if not name or not name.strip():
        return {"success": False, "error": "记忆名称不能为空"}
    if not content or not content.strip():
        return {"success": False, "error": "记忆内容不能为空"}

    name = name.strip()
    valid_types = {"user": MemoryType.USER, "feedback": MemoryType.FEEDBACK,
                   "project": MemoryType.PROJECT, "reference": MemoryType.REFERENCE}
    mem_type = valid_types.get(type.strip().lower() if type else "reference", MemoryType.REFERENCE)

    try:
        storage = MemoryStorage(MEMORY_DIR)
        doc = await storage.save(
            name=name,
            description=description.strip() if description else name,
            type_=mem_type,
            body=content.strip(),
        )
        return {
            "success": True,
            "result": {
                "name": doc.meta.name,
                "filename": Path(doc.file_path).name,
                "type": doc.meta.type.value,
                "description": doc.meta.description,
                "updated_at": doc.meta.updated_at,
                "body_preview": content.strip()[:300],
            },
        }
    except Exception as exc:
        logger.exception("memory_save failed")
        return {"success": False, "error": f"保存记忆失败: {exc}"}


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


# ── file_edit ─────────────────────────────────────────────────────────

async def file_edit_handler(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    """Perform exact string replacement in a file.

    Reads the file, finds *old_string*, replaces it with *new_string*,
    and writes the file back.  This is the preferred way to make surgical
    edits — safer and more reliable than ``file_patch`` (unified diff)
    for most single-change scenarios.

    Args:
        path: Relative path within the session workspace.
        old_string: The exact text to find and replace. Must match
            exactly, including whitespace and indentation.
        new_string: The text to replace *old_string* with.
        replace_all: If True, replace every occurrence.  If False
            (default) and *old_string* appears more than once, the
            edit is refused and the user is asked to be more specific.
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if await aisdir(safe):
        return {"success": False, "error": f"'{path}' 是一个目录，无法编辑"}

    if not old_string:
        return {"success": False, "error": "old_string 不能为空"}

    # ── Context ────────────────────────────────────────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()

    # ── Handle new file creation ─────────────────────────────────────
    # When the file doesn't exist yet, auto-create it with new_string as
    # the full content.  This makes file_edit a universal "write-or-edit"
    # tool — agents no longer need to remember to switch to file_write
    # for new files.  Matches the behaviour of the native Claude Edit tool.
    if not await aexists(safe):
        try:
            await amkdir(safe.parent)
            await awrite_text(safe, new_string, encoding="utf-8")
            size = await astat_size(safe)
        except OSError as exc:
            return {"success": False, "error": f"创建文件失败: {exc}"}

        # Track version + broadcast (fire-and-forget)
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            fv = file_version_tracker.record_write(sid, path, new_string, uid, "")
            sha256_hash = fv.sha256
        except Exception:
            pass
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, "", user_id=uid)
        )
        _asyncio.ensure_future(_auto_git_commit(path, uid, "创建（通过 file_edit）"))

        return {
            "success": True,
            "result": f"文件 '{path}' 创建成功（{size} 字节）。old_string 忽略——文件之前不存在。",
            "metadata": {
                "path": path,
                "size_bytes": size,
                "created": True,
                "sha256": sha256_hash[:12] if sha256_hash else "",
            },
        }

    # ── Read original content ─────────────────────────────────────────
    try:
        original_text = await aread_text(safe, encoding="utf-8")
    except UnicodeDecodeError:
        return {"success": False, "error": f"文件不是有效的 UTF-8 文本文件，可能是二进制文件"}
    except OSError as exc:
        return {"success": False, "error": f"读取文件失败: {exc}"}

    # ── Find matches ──────────────────────────────────────────────────
    occurrences = original_text.count(old_string)
    if occurrences == 0:
        # Provide helpful diagnostic: show the file snippet around where
        # the user might be looking, so they can spot formatting issues.
        snippet_lines = original_text.split("\n")[:20]
        snippet = "\n".join(snippet_lines)
        hint = ""
        # Check if old_string with different line endings would match
        if "\r\n" in original_text and "\n" in old_string:
            hint = " (提示: 文件使用 CRLF 换行符，old_string 是否使用了 LF？)"
        return {
            "success": False,
            "error": (
                f"在文件 '{path}' 中未找到指定的文本。"
                f"请确认 old_string 与文件内容完全一致（包括空格和缩进）。"
                f"文件开头预览:\n{snippet[:500]}"
                f"{hint}"
            ),
            "metadata": {"path": path, "occurrences": 0},
        }

    if not replace_all and occurrences > 1:
        # Show context around each match to help the user disambiguate
        match_contexts: list[str] = []
        lines = original_text.split("\n")
        for idx, line in enumerate(lines):
            if old_string in line:
                ctx = f"  行 {idx + 1}: {line.strip()[:120]}"
                match_contexts.append(ctx)
        return {
            "success": False,
            "error": (
                f"在文件 '{path}' 中找到了 {occurrences} 处匹配的文本，"
                f"但 replace_all 为 false。请提供更具体的 old_string "
                f"（包含更多上下文行）以唯一定位要修改的位置。\n"
                f"匹配位置:\n" + "\n".join(match_contexts[:10])
            ),
            "metadata": {"path": path, "occurrences": occurrences},
        }

    # ── Perform replacement ───────────────────────────────────────────
    new_text = original_text.replace(old_string, new_string) if replace_all else original_text.replace(old_string, new_string, 1)

    if new_text == original_text:
        return {"success": True, "result": f"文件 '{path}' 未发生变化（old_string 与 new_string 相同）", "metadata": {"path": path, "occurrences": occurrences, "changed": False}}

    # ── Write ──────────────────────────────────────────────────────────
    try:
        await awrite_text(safe, new_text, encoding="utf-8")
        size = await astat_size(safe)

        # ── Track version ─────────────────────────────────────────────
        sha256_hash = ""
        try:
            from app.services.file_version_tracker import file_version_tracker
            fv = file_version_tracker.record_write(sid, path, new_text, uid, "")
            sha256_hash = fv.sha256
        except Exception:
            pass

        # ── Broadcast workspace change ────────────────────────────────
        diff_preview = _compute_unified_diff(original_text, new_text, path)
        import asyncio as _asyncio
        _asyncio.ensure_future(
            _broadcast_workspace_change(sid, path, "write", size, diff_preview, user_id=uid)
        )

        # ── Auto git commit ───────────────────────────────────────────
        _asyncio.ensure_future(_auto_git_commit(path, uid, "编辑"))

        replaced_count = occurrences if replace_all else 1
        return {
            "success": True,
            "result": (
                f"文件 '{path}' 编辑成功。替换了 {replaced_count} 处匹配。"
            ),
            "metadata": {
                "path": path,
                "size_bytes": size,
                "occurrences": occurrences,
                "replaced": replaced_count,
                "sha256": sha256_hash[:12] if sha256_hash else "",
            },
        }
    except OSError as exc:
        return {"success": False, "error": f"写入文件失败: {exc}"}


# ── file_glob ─────────────────────────────────────────────────────────

async def file_glob_handler(
    pattern: str,
    path: str = ".",
) -> dict[str, Any]:
    """Find files matching a glob pattern.

    Uses standard shell-style wildcards:
      - ``*`` matches any number of characters (except path separator)
      - ``**`` matches any number of characters across directories
      - ``?`` matches a single character
      - ``[abc]`` matches one character in the brackets

    Args:
        pattern: Glob pattern, e.g. ``**/*.py``, ``src/**/*.tsx``,
            ``*.md``, ``app/services/*.py``.
        path: Directory to search within (relative to workspace root).
            Defaults to ``"."`` (workspace root).
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    ws_root = get_workspace_root()
    search_root = resolve_workspace_path(path)
    if search_root is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    if not await aexists(search_root):
        return {"success": False, "error": f"目录不存在: {path}"}

    if not await aisdir(search_root):
        return {"success": False, "error": f"'{path}' 不是目录"}

    try:
        # Use pathlib's glob — recursive if pattern contains **
        matches = list(search_root.glob(pattern))
        # Filter to files only (skip directories)
        file_matches = [m for m in matches if m.is_file()]
        # Sort for deterministic output
        file_matches.sort(key=lambda p: (str(p.parent), p.name.lower()))

        result_files: list[dict[str, Any]] = []
        for f in file_matches[:200]:  # cap at 200 results
            try:
                sz = f.stat().st_size
            except OSError:
                sz = 0
            rel = str(f.relative_to(ws_root)).replace("\\", "/")
            result_files.append({
                "path": rel,
                "size_bytes": sz,
                "size_display": f"{sz:,} B" if sz < 1024 else f"{sz / 1024:.1f} KB",
            })

        total = len(file_matches)
        truncated = total > 200
        display = result_files[:200]

        return {
            "success": True,
            "result": {
                "pattern": pattern,
                "search_path": str(search_root.relative_to(ws_root)).replace("\\", "/") or ".",
                "matches": display,
                "total_matches": total,
                "truncated": truncated,
            },
        }
    except Exception as exc:
        return {"success": False, "error": f"Glob 匹配失败: {exc}"}


# ── mkdir ─────────────────────────────────────────────────────────────

async def mkdir_handler(
    path: str,
    parents: bool = True,
) -> dict[str, Any]:
    """Create a directory in the user's per-session workspace.

    Creates the specified directory (and any missing parent directories
    when *parents* is True — the default).  This is the canonical way to
    scaffold a project directory tree without writing placeholder files.

    Args:
        path: Relative directory path within the session workspace
              (e.g. ``src/components/`` or ``src/utils``).
        parents: If True (default), create intermediate directories
                 like ``mkdir -p``.  If False, fail when the parent
                 does not exist.
    """
    from app.services.workspace_context import get_workspace_root, resolve_workspace_path

    if not path or not path.strip():
        return {"success": False, "error": "目录路径不能为空"}

    path = path.strip()
    ws_root = get_workspace_root()
    safe = resolve_workspace_path(path)
    if safe is None:
        return {"success": False, "error": f"路径 '{path}' 超出工作区允许范围"}

    # ── Already exists ──────────────────────────────────────────────
    if await aexists(safe):
        if await aisdir(safe):
            try:
                listing = (await aiterdir(safe))[:20]
                names = [str(p.relative_to(ws_root)) + ("/" if await aisdir(p) else "") for p in listing]
            except OSError:
                names = []
            return {
                "success": True,
                "result": f"目录 '{path}' 已存在（{len(names)} 项）",
                "metadata": {"path": str(safe.relative_to(ws_root)), "existed": True, "items": names},
            }
        else:
            return {"success": False, "error": f"'{path}' 已存在但是一个文件，无法创建同名目录"}

    # ── Create ──────────────────────────────────────────────────────
    try:
        await amkdir(safe, parents=parents, exist_ok=False)
    except FileNotFoundError:
        return {
            "success": False,
            "error": f"无法创建目录 '{path}'：父目录不存在（设置 parents=True 可自动创建父目录）",
        }
    except FileExistsError:
        return {"success": True, "result": f"目录 '{path}' 已存在", "metadata": {"path": str(safe.relative_to(ws_root)), "existed": True}}
    except OSError as exc:
        return {"success": False, "error": f"创建目录失败: {exc}"}

    # ── Broadcast + git ─────────────────────────────────────────────
    sid = _get_sid_fast()
    uid = _get_uid_fast()
    import asyncio as _asyncio
    _asyncio.ensure_future(
        _broadcast_workspace_change(sid, path, "mkdir", 0, "", user_id=uid)
    )
    _asyncio.ensure_future(_auto_git_commit(path, uid, "创建目录"))

    return {
        "success": True,
        "result": f"目录 '{path}' 创建成功",
        "metadata": {"path": str(safe.relative_to(ws_root)), "existed": False, "parents_created": parents},
    }
