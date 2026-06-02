from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

logger = logging.getLogger("agenthub.tools.browser")

# ── Security constraints ──────────────────────────────────────────────
MAX_PAGE_TEXT_CHARS = 50_000
DEFAULT_VIEWPORT = {"width": 1280, "height": 720}
BROWSER_TIMEOUT = 30_000  # ms


def _get_browser():
    """Lazy-import and return a Playwright browser instance (module-level singleton)."""
    global _browser_instance
    if "_browser_instance" not in globals():
        _browser_instance = None

    if _browser_instance is not None and _browser_instance.is_connected():
        return _browser_instance

    try:
        from playwright.async_api import async_playwright

        # Use a lazy-launch pattern — caller must await _ensure_browser()
        return None  # signal that browser needs launching
    except ImportError:
        return None  # Playwright not installed


async def _ensure_browser():
    """Ensure a Playwright browser is launched and return (browser, context, page)."""
    global _browser_instance, _browser_context

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright 未安装。请运行: pip install playwright && playwright install chromium"
        )

    if "_browser_instance" not in globals():
        _browser_instance = None
    if "_browser_context" not in globals():
        _browser_context = None

    if _browser_instance is None or not _browser_instance.is_connected():
        pw = await async_playwright().start()
        _browser_instance = await pw.chromium.launch(headless=True)
        _browser_context = await _browser_instance.new_context(viewport=DEFAULT_VIEWPORT)

    page = await _browser_context.new_page()
    return _browser_instance, _browser_context, page


# ── browser_navigate ──────────────────────────────────────────────────

async def browser_navigate_handler(
    url: str,
    wait_until: str = "domcontentloaded",
    timeout: int = 30,
) -> dict[str, Any]:
    """Navigate to a URL and return page info."""
    if not url or not url.strip():
        return {"success": False, "error": "URL 不能为空"}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        _, _, page = await _ensure_browser()
        effective_timeout = min(timeout * 1000, BROWSER_TIMEOUT)

        wait_state = "domcontentloaded"
        if wait_until in ("load", "networkidle", "domcontentloaded"):
            wait_state = wait_until

        await page.goto(url, wait_until=wait_state, timeout=effective_timeout)

        title = await page.title()
        page_url = page.url
        text = await page.inner_text("body")
        text = text[:MAX_PAGE_TEXT_CHARS]

        await page.close()

        return {
            "success": True,
            "result": {
                "url": page_url,
                "title": title,
                "text_preview": text[:2000],
                "total_chars": len(text),
            },
            "metadata": {"url": page_url, "title": title, "wait_until": wait_until},
        }
    except Exception as exc:
        logger.warning("browser_navigate failed url=%s: %s", url, exc)
        return {"success": False, "error": f"页面导航失败: {exc}"}


# ── browser_extract ───────────────────────────────────────────────────

async def browser_extract_handler(
    url: str,
    selector: str = "body",
    extract_type: str = "text",
    wait_for_selector: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Navigate to a URL and extract content from a CSS selector."""
    if not url or not url.strip():
        return {"success": False, "error": "URL 不能为空"}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        _, _, page = await _ensure_browser()
        effective_timeout = min(timeout * 1000, BROWSER_TIMEOUT)

        await page.goto(url, wait_until="domcontentloaded", timeout=effective_timeout)

        if wait_for_selector:
            await page.wait_for_selector(wait_for_selector, timeout=effective_timeout)

        if extract_type == "text":
            element = await page.query_selector(selector)
            if element:
                content = await element.inner_text()
            else:
                content = await page.inner_text(selector)
        elif extract_type == "html":
            element = await page.query_selector(selector)
            if element:
                content = await element.inner_html()
            else:
                content = await page.inner_html(selector)
        elif extract_type == "attribute":
            element = await page.query_selector(selector)
            content = await element.get_attribute("value") if element else ""
        else:
            content = await page.inner_text(selector)

        content = (content or "")[:MAX_PAGE_TEXT_CHARS]
        title = await page.title()

        await page.close()

        return {
            "success": True,
            "result": {
                "url": page.url,
                "title": title,
                "selector": selector,
                "content": content,
                "total_chars": len(content),
            },
            "metadata": {"url": page.url, "selector": selector, "extract_type": extract_type},
        }
    except Exception as exc:
        logger.warning("browser_extract failed url=%s: %s", url, exc)
        return {"success": False, "error": f"内容提取失败: {exc}"}


# ── browser_screenshot ────────────────────────────────────────────────

async def browser_screenshot_handler(
    url: str,
    full_page: bool = False,
    selector: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    """Take a screenshot of a URL or element and return as base64."""
    import base64

    if not url or not url.strip():
        return {"success": False, "error": "URL 不能为空"}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        _, _, page = await _ensure_browser()
        effective_timeout = min(timeout * 1000, BROWSER_TIMEOUT)

        await page.goto(url, wait_until="domcontentloaded", timeout=effective_timeout)

        if selector:
            element = await page.query_selector(selector)
            if element:
                screenshot_bytes = await element.screenshot()
            else:
                screenshot_bytes = await page.screenshot(full_page=full_page)
        else:
            screenshot_bytes = await page.screenshot(full_page=full_page)

        screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")

        await page.close()

        return {
            "success": True,
            "result": {
                "url": page.url,
                "screenshot_base64": screenshot_b64,
                "size_bytes": len(screenshot_bytes),
            },
            "metadata": {
                "url": page.url,
                "full_page": full_page,
                "selector": selector,
                "size_bytes": len(screenshot_bytes),
            },
        }
    except Exception as exc:
        logger.warning("browser_screenshot failed url=%s: %s", url, exc)
        return {"success": False, "error": f"截图失败: {exc}"}


# ── browser_click ─────────────────────────────────────────────────────

async def browser_click_handler(
    url: str,
    selector: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """Navigate to a URL and click an element, returning the resulting page info."""
    if not url or not url.strip():
        return {"success": False, "error": "URL 不能为空"}
    if not selector or not selector.strip():
        return {"success": False, "error": "selector 不能为空"}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        _, _, page = await _ensure_browser()
        effective_timeout = min(timeout * 1000, BROWSER_TIMEOUT)

        await page.goto(url, wait_until="domcontentloaded", timeout=effective_timeout)
        await page.wait_for_selector(selector, timeout=effective_timeout)
        await page.click(selector)

        # Wait for navigation or network idle
        await page.wait_for_load_state("networkidle", timeout=effective_timeout)

        title = await page.title()
        new_url = page.url
        text = await page.inner_text("body")
        text = text[:MAX_PAGE_TEXT_CHARS]

        await page.close()

        return {
            "success": True,
            "result": {
                "url": new_url,
                "title": title,
                "text_preview": text[:2000],
                "total_chars": len(text),
            },
            "metadata": {"original_url": url, "new_url": new_url, "selector": selector},
        }
    except Exception as exc:
        logger.warning("browser_click failed url=%s selector=%s: %s", url, selector, exc)
        return {"success": False, "error": f"点击操作失败: {exc}"}


# ── browser_type ──────────────────────────────────────────────────────

async def browser_type_handler(
    url: str,
    selector: str,
    text: str,
    press_enter: bool = False,
    timeout: int = 30,
) -> dict[str, Any]:
    """Navigate to a URL, type text into an input, and optionally submit."""
    if not url or not url.strip():
        return {"success": False, "error": "URL 不能为空"}
    if not selector or not selector.strip():
        return {"success": False, "error": "selector 不能为空"}
    if not text:
        return {"success": False, "error": "输入文本不能为空"}

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        _, _, page = await _ensure_browser()
        effective_timeout = min(timeout * 1000, BROWSER_TIMEOUT)

        await page.goto(url, wait_until="domcontentloaded", timeout=effective_timeout)
        await page.wait_for_selector(selector, timeout=effective_timeout)

        await page.fill(selector, text)

        if press_enter:
            await page.press(selector, "Enter")
            await page.wait_for_load_state("networkidle", timeout=effective_timeout)

        new_title = await page.title()
        new_url = page.url
        body_text = await page.inner_text("body")
        body_text = body_text[:MAX_PAGE_TEXT_CHARS]

        await page.close()

        return {
            "success": True,
            "result": {
                "url": new_url,
                "title": new_title,
                "text_preview": body_text[:2000],
                "total_chars": len(body_text),
            },
            "metadata": {"url": new_url, "selector": selector, "press_enter": press_enter},
        }
    except Exception as exc:
        logger.warning("browser_type failed url=%s: %s", url, exc)
        return {"success": False, "error": f"输入操作失败: {exc}"}
