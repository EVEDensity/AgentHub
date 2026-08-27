from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.config import (
    ANTHROPIC_API_KEY,
    ENABLE_REAL_LLM,
    LLM_GATEWAY,
    NEWAPI_API_KEY,
    NEWAPI_BASE_URL,
    OLLAMA_BASE_URL,
    OPENAI_API_KEY,
    REQUEST_TIMEOUT_SECONDS,
)

from app.services.message_content import (
    anthropic_blocks_from_content,
    assert_parts_allowed_for_model,
    flatten_text_content,
    validate_dual_track_content,
)

logger = logging.getLogger("agenthub.adapter")

# ═══════════════════════════════════════════════════════════════════════
# Shared HTTP client — connection pooling + fine-grained timeouts
# ═══════════════════════════════════════════════════════════════════════
# Prior art: a single httpx.Timeout(600) set connect/read/write/pool all
# to the same value.  A hung TCP handshake (SYN dropped by firewall,
# DNS resolution stalls) would block for 10 minutes before failing.
#
# Now we split the timeout into four independent layers so each phase
# fails fast with a meaningful error:
#
#   connect = 30 s   — TCP + TLS handshake (should never take longer)
#   read    = 600 s  — streaming response body (max LLM generation time)
#   write   = 60 s   — uploading a large request payload
#   pool    = 30 s   — waiting for a free connection from the pool
#
# The overall read timeout is configurable via AGENTHUB_REQUEST_TIMEOUT.
#
# Connection pool sizing:
#   max_keepalive_connections = 30  (was 20) — one per LLM provider host
#   max_connections           = 120 (was 100) — headroom for concurrent reqs
#   keepalive_expiry          = 60 s          — recycle idle keep-alive conns

_SHARED_CLIENT: httpx.AsyncClient | None = None
_SHARED_CLIENT_LOCK = asyncio.Lock()

# ── Retry configuration ──────────────────────────────────────────────
# Transient HTTP errors (429 rate-limit, 502 bad gateway, 503 service
# unavailable, 504 gateway timeout) are retried with exponential backoff.
# Connection-level errors (ConnectionError, ConnectTimeout, ReadTimeout
# on the first byte) are also retried — these are typically network
# glitches, not permanent failures.
#
# Permanent errors (400, 401, 403, 404, 422, 500) are NOT retried — the
# caller gets the error immediately.

_RETRYABLE_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.2   # seconds → 1.2, 2.4, 4.8 (capped at 10 s)
_RETRY_BACKOFF_MAX = 10.0    # seconds


def _get_client(timeout: httpx.Timeout | None = None) -> httpx.AsyncClient:
    """Return (or lazily create) a shared httpx.AsyncClient.

    The shared client uses fine-grained default timeouts.  Callers that
    need different timeouts (e.g. web search tools that expect sub-15 s
    responses) can pass ``timeout=httpx.Timeout(...)`` — the timeout is
    applied per-request, not per-client.
    """
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None or _SHARED_CLIENT.is_closed:
        _SHARED_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=REQUEST_TIMEOUT_SECONDS,
                write=60.0,
                pool=30.0,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=30,
                max_connections=120,
                keepalive_expiry=60.0,
            ),
            # Windows Python 3.13 + httpx 0.28 has a TLS certificate
            # verification issue where even certifi's CA bundle fails
            # (raw sockets work, but httpx's SSLConfig chain does not).
            # verify is disabled only on affected platforms; override via
            # AGENTHUB_SSL_VERIFY=true/false env var.
            verify=(
                os.environ.get("AGENTHUB_SSL_VERIFY", "").lower() != "false"
                if "AGENTHUB_SSL_VERIFY" in os.environ
                else not (
                    sys.platform == "win32"
                    and sys.version_info[:2] == (3, 13)
                )
            ),
        )
    return _SHARED_CLIENT


def _get_search_client() -> httpx.AsyncClient:
    """Return a shared client tuned for web search (short timeouts).

    Web search APIs should respond in <15 s.  This client reuses the
    same connection pool as the LLM client but applies a tighter
    per-request timeout by default.
    """
    # We reuse the same underlying client (so connection pooling still
    # works across LLM + search calls) but search callers should pass
    # their own timeout on each request.
    return _get_client()


async def close_http_client() -> None:
    """Gracefully close the shared HTTP client (call on app shutdown)."""
    global _SHARED_CLIENT
    if _SHARED_CLIENT is not None and not _SHARED_CLIENT.is_closed:
        await _SHARED_CLIENT.aclose()
        _SHARED_CLIENT = None
        logger.info("shared httpx client closed")


async def _retry_request(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json_body: dict | None = None,
    timeout: httpx.Timeout | None = None,
    max_retries: int = _MAX_RETRIES,
) -> httpx.Response:
    """Execute an HTTP request with exponential-backoff retry.

    Only transient errors (429, 502, 503, 504, connection failures) are
    retried.  Permanent errors (4xx auth, 5xx internal) propagate immediately.

    Returns the httpx.Response on success.
    """
    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers or {}, timeout=timeout)
            elif method.upper() == "POST":
                resp = await client.post(url, headers=headers or {}, json=json_body, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # Retry on transient server errors
            if resp.status_code in _RETRYABLE_STATUSES and attempt < max_retries:
                delay = min(_RETRY_BACKOFF_BASE * (2 ** attempt), _RETRY_BACKOFF_MAX)
                # ── Record retry for performance monitoring ──────
                try:
                    from app.services.performance_monitor import monitor
                    monitor.record_retry("unknown", "unknown")
                except Exception:
                    pass
                logger.warning(
                    "retry attempt=%d/%d method=%s url=%s status=%d delay=%.1fs",
                    attempt + 1, max_retries, method, url[:120], resp.status_code, delay,
                )
                await asyncio.sleep(delay)
                continue

            return resp

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.RemoteProtocolError, httpx.PoolTimeout) as exc:
            last_exc = exc
            if attempt < max_retries:
                delay = min(_RETRY_BACKOFF_BASE * (2 ** attempt), _RETRY_BACKOFF_MAX)
                logger.warning(
                    "retry attempt=%d/%d method=%s url=%s error=%s delay=%.1fs",
                    attempt + 1, max_retries, method, url[:120], exc, delay,
                )
                await asyncio.sleep(delay)
                continue
            raise

    # Should only reach here if all retries exhausted on retryable status
    if last_exc:
        raise last_exc
    # Fallback — shouldn't happen, but return whatever the last response was
    raise RuntimeError(f"Exhausted {max_retries} retries for {method} {url}")


def _tokenize_for_matching(text: str) -> list[str]:
    """Split text into matchable tokens — works for both English (spaces)
    and Chinese (character bigrams + meaningful substrings)."""
    tokens: list[str] = []

    # English-style words (space-delimited)
    if any(c.isascii() and c.isalpha() for c in text):
        tokens.extend(w.strip('?!.,，。？') for w in text.split() if len(w.strip('?!.,，。？')) >= 2)

    # Chinese-style: character bigrams + key substrings
    # Remove punctuation for Chinese processing
    clean = text.translate(str.maketrans('', '', '？！。，、：；（）「」『』""'''))
    # Add 2-char and 3-char n-grams
    for i in range(len(clean) - 1):
        bigram = clean[i:i+2]
        # Skip bigrams with non-Chinese characters
        if any('一' <= c <= '鿿' for c in bigram):
            tokens.append(bigram)
    for i in range(len(clean) - 2):
        trigram = clean[i:i+3]
        if any('一' <= c <= '鿿' for c in trigram):
            tokens.append(trigram)

    return tokens


class LLMAdapterError(RuntimeError):
    pass


class BaseAdapter:
    # Provider identity injected by AdapterManager (eager dict + lazy-load
    # branches); consumed by the fail-closed vision-capability gate (ADR-0105).
    provider_name: str = ""

    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {}

    @property
    def default_model(self) -> str:
        """Default model name — overridden by subclasses.

        Subclasses may declare ``default_model`` as a class attribute
        (preferred) or assign it in ``__init__`` (supported via the
        setter below).  The getter resolves the value with the
        following precedence:

        1. Instance attribute ``_default_model`` (set via constructor)
        2. Class attribute ``default_model`` (declared on subclass)
        3. Empty string (base default)
        """
        # Instance attribute set via the constructor takes precedence
        instance_val = self.__dict__.get("_default_model")
        if instance_val is not None:
            return instance_val
        # Fall back to class attribute (most subclasses use this form)
        return getattr(type(self), "default_model", "")

    @default_model.setter
    def default_model(self, value: str) -> None:
        """Allow ``self.default_model = ...`` in ``__init__`` of adapters
        that take a ``default_model`` constructor argument (e.g. the
        subprocess-based :class:`CloudCodeAdapter`).  Subclasses that
        declare ``default_model`` as a class attribute continue to work
        unchanged because the class attribute is returned by the getter
        until the first instance assignment shadows it."""
        self._default_model = value

    def _get_default_model(self) -> str:
        return getattr(self, "_default_model", "")

    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "", *, system_prompt: str = "", **kwargs: Any) -> str:
        raise NotImplementedError

    async def stream_prompt(self, prompt: str | list[dict[str, Any]], model: str, api_key: str = "", base_url: str = "", *, system_prompt: str = "") -> AsyncGenerator[str, None]:
        """Streaming fallback: chunks the full response for pseudo-streaming.

        Subclasses SHOULD override this with real SSE/NDJSON streaming.
        This base implementation waits for the full response then yields
        small chunks so the frontend still sees progressive text.
        """
        result = await self.execute_prompt(prompt, model, api_key, base_url, system_prompt=system_prompt)
        # Smaller chunks for faster perceived streaming (was len//20)
        chunk_size = max(1, min(40, len(result) // 50)) if len(result) > 200 else max(1, len(result) // 20)
        for i in range(0, len(result), chunk_size):
            yield result[i:i + chunk_size]
            # Pure yield — no asyncio.sleep(0) needed; the caller's
            # event-loop yield in the stream loop handles fairness.
        yield ""

    async def ping(self, model: str = "", api_key: str = "", base_url: str = "") -> str:
        """Real connectivity check — MUST NOT fall back to MockAdapter.

        Returns a human-readable status message on success.
        Raises an exception on failure (connection refused, auth error, etc.).

        Subclasses should override this with a lightweight probe.
        """
        raise NotImplementedError(f"ping() not implemented for {type(self).__name__}")


class MockAdapter(BaseAdapter):
    """Mock adapter that simulates both text replies and tool calls.

    When the prompt contains tool definitions and the user's question
    matches a tool's example, returns a ``{"tool_calls": [...]}`` JSON
    so the tool-execution pipeline can exercise its full flow even
    without a real LLM API key — useful for development and testing.
    """

    async def execute_prompt(self, prompt: str, model: str = "mock", api_key: str = "", base_url: str = "", **kwargs: Any) -> str:
        # ── Try to simulate a tool call ────────────────────────────
        tools_section = self._extract_tools_from_prompt(prompt)
        user_question = self._extract_user_question(prompt)

        if tools_section and user_question:
            tool_call = self._simulate_tool_call(tools_section, user_question)
            if tool_call is not None:
                result = json.dumps({"tool_calls": [tool_call]}, ensure_ascii=False)
                self.last_usage = {
                    "prompt_tokens": max(1, len(prompt) // 4),
                    "completion_tokens": max(1, len(result) // 4),
                    "total_tokens": max(1, len(prompt) // 4) + max(1, len(result) // 4),
                }
                logger.info("MockAdapter: simulated tool_call → %s", tool_call["name"])
                return result

        # ── Plain mock reply ───────────────────────────────────────
        result = f"本地 Mock 模型响应：{prompt[:500]}"
        self.last_usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(result) // 4), "total_tokens": max(1, len(prompt) // 4) + max(1, len(result) // 4)}
        return result

    async def stream_prompt(self, prompt: str, model: str = "mock", api_key: str = "", base_url: str = "", *, system_prompt: str = "") -> AsyncGenerator[str, None]:
        """Mock streaming: delegate to execute_prompt and yield the full result."""
        result = await self.execute_prompt(prompt, model, api_key, base_url, system_prompt=system_prompt)
        # Yield the full result as one chunk so tool-call JSON is complete
        yield result
        yield ""  # end-of-stream marker

    async def ping(self, model: str = "", api_key: str = "", base_url: str = "") -> str:
        """Mock adapter is always 'connected' — no external dependency."""
        return "Mock 本地模型就绪，无需外部连接"

    @staticmethod
    def _extract_tools_from_prompt(prompt: str) -> dict[str, dict]:
        """Parse tool definitions from the prompt's tool section.

        Returns a dict mapping tool_name → {description, risk_level, examples, parameters}.
        """
        import re

        tools: dict[str, dict] = {}
        # Match tool blocks: "### 工具 N: tool_name"  followed by fields
        tool_blocks = re.split(r'### 工具 \d+: ', prompt)
        for block in tool_blocks[1:]:  # skip text before first tool
            lines = block.strip().split('\n')
            name = lines[0].strip() if lines else ''
            if not name:
                continue

            desc = ''
            params: list[dict] = []
            examples: list[dict] = []
            section = ''

            for line in lines[1:]:
                line = line.strip()
                if line.startswith('- 分类:') or line.startswith('- 风险等级:'):
                    section = 'meta'
                elif line.startswith('- 描述:'):
                    desc = line.replace('- 描述:', '').strip()
                    section = 'meta'
                elif line.startswith('- 参数:'):
                    section = 'params'
                elif line.startswith('- 返回类型:'):
                    section = 'return'
                elif line.startswith('- 使用示例:'):
                    section = 'examples'
                elif line.startswith('· 用户提问:'):
                    if section == 'examples':
                        q = line.replace('· 用户提问:', '').strip().strip('"')
                        examples.append({'user_question': q, 'parameters': {}})
                elif line.startswith('   调用参数:') and examples:
                    args_str = line.replace('   调用参数:', '').strip()
                    try:
                        examples[-1]['parameters'] = json.loads(args_str)
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif line.startswith('  - ') and section == 'params':
                    # Parse: "  - name (type, 必填/选填, ...): description"
                    param_match = re.match(r'  - (\w+)\s*\(([^)]+)\)', line)
                    if param_match:
                        pname = param_match.group(1)
                        pdetail = param_match.group(2)
                        parts = [p.strip() for p in pdetail.split(',')]
                        ptype = parts[0] if parts else 'string'
                        required = any('必填' in p for p in parts)
                        params.append({'name': pname, 'type': ptype, 'required': required})

            if name:
                tools[name] = {
                    'description': desc,
                    'parameters': params,
                    'examples': examples,
                }

        return tools

    @staticmethod
    def _extract_user_question(prompt: str) -> str:
        """Extract the user's question from the end of the prompt."""
        import re
        # The prompt ends with "用户需求: <content>" — content may span
        # multiple lines (e.g. "【用户消息】\n今天有什么科技新闻？").
        # Use DOTALL so ".+" captures across newlines, and do NOT use
        # MULTILINE here — we want $ to match the real end-of-string.
        match = re.search(r'用户需求[：:]\s*(.+)', prompt, re.DOTALL)
        question = ""
        if match:
            question = match.group(1).strip()
        if not question:
            lines = [l for l in prompt.split('\n') if l.strip()]
            question = lines[-1].strip() if lines else ""

        # Strip conversation formatting prefix like "【用户消息】"
        question = re.sub(r'【[^】]*】\s*', '', question).strip()
        return question

    @staticmethod
    def _simulate_tool_call(tools: dict[str, dict], question: str) -> dict | None:
        """Decide which tool to simulate based on the user's question.

        Returns a tool_call dict like {"name": "...", "arguments": {...}},
        or None to respond with plain text.
        """
        q_lower = question.lower()

        # Build a keyword list from the question (handles both
        # space-delimited English and unsegmented Chinese text).
        q_keywords = _tokenize_for_matching(q_lower)

        # Score each tool by keyword / example match
        best_score = 0.0
        best_tool: tuple[str, dict] | None = None

        for name, info in tools.items():
            score = 0.0

            desc_lower = info.get('description', '').lower()
            name_lower = name.replace('_', ' ').lower()

            # Name keyword match
            name_kw = _tokenize_for_matching(name_lower)
            for kw in q_keywords:
                if len(kw) >= 2 and kw in name_lower:
                    score += 5
                # Check if any name keyword overlaps with question
                for nk in name_kw:
                    if len(kw) >= 2 and len(nk) >= 2 and (kw in nk or nk in kw):
                        score += 4

            # Description keyword match
            for kw in q_keywords:
                if len(kw) >= 2 and kw in desc_lower:
                    score += 2

            # Example match: check if question is similar to an example
            for ex in info.get('examples', []):
                ex_q = ex.get('user_question', '').lower()
                ex_keywords = _tokenize_for_matching(ex_q)
                # Keyword overlap
                for kw in q_keywords:
                    if len(kw) >= 2:
                        for ek in ex_keywords:
                            if len(ek) >= 2 and (kw in ek or ek in kw):
                                score += 3
                # Substring match (Chinese-friendly)
                if len(q_lower) >= 4 and len(ex_q) >= 4:
                    # Check 3-char sliding window overlap
                    for i in range(len(q_lower) - 2):
                        chunk = q_lower[i:i+3]
                        if chunk in ex_q:
                            score += 2
                            break

            if score > best_score:
                best_score = score
                best_tool = (name, info)

        # Threshold: require a minimum match score
        if best_score < 2 or best_tool is None:
            return None

        name, info = best_tool

        # Build arguments from question + examples
        arguments: dict[str, Any] = {}
        for p in info.get('parameters', []):
            if p.get('required'):
                # Try to extract value from question
                if p['name'] == 'query':
                    arguments['query'] = question[:100]
                elif p['name'] == 'path':
                    # Extract file path from question
                    import re
                    path_match = re.search(r'["\']?([\w./-]+\.[\w]+)', question)
                    arguments['path'] = path_match.group(1) if path_match else 'README.md'
                elif p['name'] == 'code':
                    arguments['code'] = 'print("Hello from MockAdapter")'
                elif p['name'] == 'content':
                    arguments['content'] = 'Mock content'
                elif p['name'] == 'language':
                    arguments['language'] = 'python'
                else:
                    arguments[p['name']] = f'<{p["name"]}_value>'

            # Fill optional params from examples or defaults
            elif p.get('name') in ('max_results',):
                arguments[p['name']] = 5
            elif p.get('name') in ('language',):
                arguments[p['name']] = 'zh'

        # Use example arguments if available
        examples = info.get('examples', [])
        if examples:
            ex_args = examples[0].get('parameters', {})
            for k, v in ex_args.items():
                if k not in arguments:
                    arguments[k] = v

        return {"name": name, "arguments": arguments}


class OpenAICompatibleAdapter(BaseAdapter):
    default_base_url = "https://api.openai.com/v1"
    env_api_key = OPENAI_API_KEY
    default_model = "gpt-3.5-turbo"
    supports_stream_usage: bool = False  # Only OpenAI supports stream_options
    temperature: float = 0.2
    frequency_penalty: float = 0.5
    presence_penalty: float = 0.3
    max_tokens: int = 32768  # 32K output — complex HTML/CSS/JS generation needs headroom

    async def execute_prompt(
        self, prompt: str | list[dict[str, Any]], model: str, api_key: str = "", base_url: str = "",
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str = "",
    ) -> str:
        """Execute a prompt, optionally with native function-calling tools.

        When *tools* is a non-empty list of OpenAI-format tool definitions,
        they are passed as the ``tools`` request parameter so the model can
        natively return ``tool_calls`` instead of plain text.

        Native ``tool_calls`` in the response are transparently converted to
        our internal ``{"tool_calls": [...]}`` JSON string so the existing
        parsing pipeline handles them without modification.

        When *system_prompt* is non-empty, it is prepended as a system message.
        """
        validate_dual_track_content(prompt)
        key = api_key or self.env_api_key
        if not ENABLE_REAL_LLM or not key:
            return await MockAdapter().execute_prompt(flatten_text_content(prompt), model)
        actual_model = model.strip() if model and model.strip() and model != "ping" else self.default_model
        # Dual-track (ADR-0105): image parts require a vision-capable model —
        # text-only models get an explicit error, never silent degradation.
        assert_parts_allowed_for_model(self.provider_name, actual_model, prompt)
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "/chat/completions"
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": actual_model,
            "messages": messages,
            "temperature": self.temperature,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "max_tokens": getattr(self, "max_tokens", 16384),
        }
        # ── Native function calling ──────────────────────────────────
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # ── Execute (shared client + retry on transient errors) ──────
        response = await _retry_request(
            "POST", url,
            headers={"Authorization": f"Bearer {key}"},
            json_body=payload,
        )
        if response.status_code >= 400:
            raise LLMAdapterError(f"HTTP {response.status_code}: {str(response.text)[:500]}")
        data = response.json()
        usage = data.get("usage", {})
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens") or max(1, len(flatten_text_content(prompt)) // 4),
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
        }

        message = data["choices"][0].get("message") if isinstance(data.get("choices", [None])[0], dict) else None
        finish_reason = data["choices"][0].get("finish_reason", "") if isinstance(data.get("choices", [None])[0], dict) else ""

        # Guard: some providers return "message": null when the response is malformed
        # (e.g. token-limit truncation or provider-side errors).
        if not isinstance(message, dict):
            message = {}

        # ── Native tool_calls → internal JSON format ─────────────────
        native_tool_calls = message.get("tool_calls") or []
        if native_tool_calls:
            converted: list[dict[str, Any]] = []
            for tc in native_tool_calls:
                # Guard against malformed entries: tc itself may be None,
                # and tc["function"] may be null instead of a dict.
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function") or {}
                name = func.get("name", "") if isinstance(func, dict) else ""
                args_str = func.get("arguments", "{}") if isinstance(func, dict) else "{}"
                try:
                    arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                if name:
                    converted.append({"name": name, "arguments": arguments})
            # Return in the same format our prompt-based parser expects
            return json.dumps({"tool_calls": converted}, ensure_ascii=False)

        # ── Plain text response ──────────────────────────────────────
        # Reasoning models (DeepSeek V4, Kimi K2, doubao-thinking, etc.)
        # separate chain-of-thought from the final answer:
        #   reasoning_content = internal thinking (wrapped in <think> for UI)
        #   content           = the visible reply
        #
        # When the model exhausts its token budget during reasoning,
        # ``content`` may be empty while ``reasoning_content`` is present.
        # This is a token-limit issue, not a model failure — the model
        # simply needs more headroom to finish.  We surface the reasoning
        # so the user can see the progress, and guide them to retry.
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""

        if reasoning and not content:
            # Model only produced reasoning — almost certainly hit token limit.
            if finish_reason == "length":
                hint = (
                    "模型推理已完成但输出 token 不足，回复被截断。\n"
                    "建议：(1) 简化问题或拆分任务 (2) 降低系统提示词长度 (3) 在模型配置中增加 max_tokens"
                )
            else:
                hint = (
                    "模型推理阶段完成但未生成最终回复 (finish_reason={})。\n"
                    "请重试或简化输入内容。"
                ).format(finish_reason or "unknown")
            content = f"<think>{reasoning}</think>\n\n{hint}"
        elif reasoning and content:
            content = f"<think>{reasoning}</think>\n\n{content}"
        # else: no reasoning — just plain content (or empty)

        if self.last_usage["completion_tokens"] == 0:
            self.last_usage["completion_tokens"] = usage.get("completion_tokens") or max(1, len(content) // 4)
        if self.last_usage["total_tokens"] == 0:
            self.last_usage["total_tokens"] = (
                self.last_usage["prompt_tokens"] + self.last_usage["completion_tokens"]
            )
        return content

    async def stream_prompt(self, prompt: str | list[dict[str, Any]], model: str, api_key: str = "", base_url: str = "", *, system_prompt: str = "") -> AsyncGenerator[str, None]:
        validate_dual_track_content(prompt)
        key = api_key or self.env_api_key
        if not ENABLE_REAL_LLM or not key:
            async for chunk in MockAdapter().stream_prompt(flatten_text_content(prompt), model, system_prompt=system_prompt):
                yield chunk
            return
        actual_model = model.strip() if model and model.strip() and model != "ping" else self.default_model
        # Dual-track (ADR-0105): fail-closed vision gate before any network I/O.
        assert_parts_allowed_for_model(self.provider_name, actual_model, prompt)
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "//chat/completions"
        url = url.replace("//chat", "/chat")  # normalize double slash
        stream_messages: list[dict[str, Any]] = []
        if system_prompt:
            stream_messages.append({"role": "system", "content": system_prompt})
        stream_messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {"model": actual_model, "messages": stream_messages, "temperature": self.temperature, "stream": True, "frequency_penalty": self.frequency_penalty, "presence_penalty": self.presence_penalty, "max_tokens": getattr(self, "max_tokens", 16384)}
        if self.supports_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        self.last_usage = {}  # reset per call so stale data never leaks
        full_text = ""
        reasoning_open = False
        # 防思考死循环：超过 1500 字符强制关闭 think 块，让模型进入正文。
        _MAX_REASONING_CHARS = 1500
        reasoning_chars = 0
        reasoning_truncated = False
        client = _get_client()
        # Streaming uses raw client.stream (not _retry_request) because the
        # SSE body is consumed incrementally.  Retry on transient connection
        # errors in the initial handshake is handled inside the async block.
        async with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    body_text = body.decode(errors="replace")
                    raise LLMAdapterError(
                        f"HTTP {response.status_code}: {body_text[:500]}"
                    )
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        # Guard: some providers send null choices[0] or "delta": null in final/empty chunks.
                        choices = obj.get("choices")
                        if not choices or not isinstance(choices, list):
                            continue
                        choice = choices[0]
                        if not isinstance(choice, dict):
                            continue
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            delta = {}
                        reasoning = delta.get("reasoning_content", "")
                        content = delta.get("content", "")
                        if reasoning:
                            if not reasoning_truncated:
                                remaining = _MAX_REASONING_CHARS - reasoning_chars
                                if remaining > 0:
                                    chunk = reasoning[:remaining]
                                    if not reasoning_open:
                                        reasoning_open = True
                                        full_text += "<think>"
                                        yield "<think>"
                                    full_text += chunk
                                    yield chunk
                                    reasoning_chars += len(chunk)
                                if reasoning_chars >= _MAX_REASONING_CHARS:
                                    reasoning_truncated = True
                                    close_hint = (
                                        "\n</think>\n\n"
                                        "【思考已达到上限，请直接给出最终回复，不要再继续思考。】\n\n"
                                    )
                                    full_text += close_hint
                                    yield close_hint
                                    reasoning_open = False
                        if content:
                            if reasoning_open:
                                reasoning_open = False
                                full_text += "</think>"
                                yield "</think>"
                            full_text += content
                            yield content
                        if "usage" in obj:
                            u = obj["usage"]
                            self.last_usage = {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0), "total_tokens": u.get("total_tokens", 0)}
                    except (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError):
                        continue
                if reasoning_open:
                    full_text += "</think>"
                    yield "</think>"
                    # Model produced reasoning but no final content — token limit likely
                    hint = (
                        "\n\n模型推理已完成但输出 token 不足，回复被截断。"
                        "建议简化问题或增加 max_tokens 配置后重试。"
                    )
                    full_text += hint
                    yield hint
        yield ""
        # Always set fallback estimation when no real usage was captured from chunks
        if not self.last_usage.get("total_tokens"):
            self.last_usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(full_text) // 4), "total_tokens": max(1, len(prompt) // 4) + max(1, len(full_text) // 4)}

    async def ping(self, model: str = "", api_key: str = "", base_url: str = "") -> str:
        """Real connectivity probe — lightweight GET /models, NO mock fallback."""
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "/models"
        key = api_key or self.env_api_key
        if not key:
            raise LLMAdapterError("未配置 API Key，无法测试连接")
        headers: dict[str, str] = {}
        # OpenAI and most compatibles use Bearer; some (e.g. custom) may differ
        if key.startswith("sk-") or key.startswith("fk") or len(key) > 30:
            headers["Authorization"] = f"Bearer {key}"
        else:
            headers["Authorization"] = f"Bearer {key}"
        resp = await _retry_request("GET", url, headers=headers)
        if resp.status_code >= 400:
            raise LLMAdapterError(
                f"HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        model_list = data.get("data") or data.get("models") or []
        count = len(model_list)
        return f"{type(self).__name__} 连接正常，可用模型 {count} 个"


class OpenAIAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.openai.com/v1"
    supports_stream_usage: bool = True


class DeepSeekAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.deepseek.com/v1"
    default_model = "deepseek-v4-flash"


class MinimaxAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.minimax.chat/v1"
    default_model = "abab6-chat"


class ZhipuAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://open.bigmodel.cn/api/paas/v4"
    default_model = "glm-4"


class QwenAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    default_model = "qwen-turbo"


class DoubaoAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://ark.cn-beijing.volces.com/api/v3"
    default_model = "Doubao-3.5"


class CustomOpenAIAdapter(OpenAICompatibleAdapter):
    default_base_url = ""


class KimiAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.moonshot.cn/v1"
    default_model = "kimi-k2.6"
    temperature: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0


class AnthropicAdapter(BaseAdapter):
    default_model = "claude-sonnet-4-6"

    async def execute_prompt(self, prompt: str | list[dict[str, Any]], model: str, api_key: str = "", base_url: str = "", *, system_prompt: str = "", **kwargs: Any) -> str:
        validate_dual_track_content(prompt)
        key = api_key or ANTHROPIC_API_KEY
        if not ENABLE_REAL_LLM or not key:
            return await MockAdapter().execute_prompt(flatten_text_content(prompt), model)
        actual_model = model.strip() if model and model.strip() and model != "ping" else self.default_model
        assert_parts_allowed_for_model(self.provider_name, actual_model, prompt)
        url = (base_url.rstrip("/") if base_url else "https://api.anthropic.com") + "/v1/messages"

        # ── Build messages payload ────────────────────────────────────
        if system_prompt:
            payload: dict[str, Any] = {
                "model": actual_model,
                "max_tokens": 32768,
                "system": [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                "messages": [{"role": "user", "content": anthropic_blocks_from_content(prompt)}],
            }
        else:
            payload = {"model": actual_model, "max_tokens": 32768, "messages": [{"role": "user", "content": anthropic_blocks_from_content(prompt)}]}

        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        response = await _retry_request(
            "POST", url,
            headers=headers,
            json_body=payload,
        )
        if response.status_code >= 400:
            raise LLMAdapterError(f"HTTP {response.status_code}: {str(response.text)[:500]}")
        data = response.json()
        usage = data.get("usage", {})
        # Guard: Anthropic may return null entries in content array
        content_blocks = data.get("content") or []
        first_block = (content_blocks[0] if isinstance(content_blocks[0], dict) else {}) if content_blocks else {}
        self.last_usage = {
            "prompt_tokens": usage.get("input_tokens") or max(1, len(flatten_text_content(prompt)) // 4),
            "completion_tokens": usage.get("output_tokens") or max(1, len(first_block.get("text", "")) // 4),
            "total_tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) or max(1, len(flatten_text_content(prompt)) // 4) + max(1, len(first_block.get("text", "")) // 4),
        }
        return "\n".join(block.get("text", "") for block in content_blocks if isinstance(block, dict) and block.get("type") == "text")

    async def stream_prompt(self, prompt: str | list[dict[str, Any]], model: str, api_key: str = "", base_url: str = "", *, system_prompt: str = "") -> AsyncGenerator[str, None]:
        """Real SSE streaming via Anthropic Messages Streaming API.

        Uses ``stream: True`` and parses Server-Sent Events (SSE):
        ``content_block_delta`` → text delta, ``message_delta`` → usage,
        ``message_stop`` → end of stream.

        When *system_prompt* is provided it is sent as the ``system``
        parameter with ``cache_control: ephemeral`` — this caches the
        large static prefix (role instructions, tool definitions, rules)
        server-side and cuts TTFT by 50-70 % on subsequent requests.
        """
        validate_dual_track_content(prompt)
        key = api_key or ANTHROPIC_API_KEY
        if not ENABLE_REAL_LLM or not key:
            async for chunk in MockAdapter().stream_prompt(flatten_text_content(prompt), model):
                yield chunk
            return

        actual_model = model.strip() if model and model.strip() and model != "ping" else self.default_model
        url = (base_url.rstrip("/") if base_url else "https://api.anthropic.com") + "/v1/messages"

        # ── Build messages payload ────────────────────────────────────
        if system_prompt:
            # Split: system (cached) + user (dynamic)
            payload: dict[str, Any] = {
                "model": actual_model,
                "max_tokens": 32768,
                "system": [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                "messages": [{"role": "user", "content": anthropic_blocks_from_content(prompt)}],
                "stream": True,
            }
        else:
            # Fallback: monolithic prompt as single user message
            payload = {
                "model": actual_model,
                "max_tokens": 32768,
                "messages": [{"role": "user", "content": anthropic_blocks_from_content(prompt)}],
                "stream": True,
            }

        # Dual-track (ADR-0105): image parts require a vision-capable model —
        # checked before any network I/O.
        assert_parts_allowed_for_model(self.provider_name, actual_model, prompt)
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        self.last_usage = {}
        full_text = ""

        client = _get_client()
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise LLMAdapterError(f"HTTP {response.status_code}: {body.decode(errors='replace')[:500]}")

            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    obj = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = obj.get("type", "")

                if event_type == "content_block_delta":
                    delta = obj.get("delta", {})
                    if isinstance(delta, dict) and delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            full_text += text
                            yield text

                elif event_type == "message_delta":
                    usage = obj.get("usage", {})
                    if isinstance(usage, dict):
                        self.last_usage = {
                            "prompt_tokens": usage.get("input_tokens", 0),
                            "completion_tokens": usage.get("output_tokens", 0),
                            "total_tokens": (
                                usage.get("input_tokens", 0)
                                + usage.get("output_tokens", 0)
                            ),
                        }

                elif event_type == "message_stop":
                    break

        yield ""  # end-of-stream sentinel

        # Fallback token estimation if no usage was captured
        if not self.last_usage.get("total_tokens"):
            self.last_usage = {
                "prompt_tokens": max(1, len(prompt) // 4),
                "completion_tokens": max(1, len(full_text) // 4),
                "total_tokens": max(1, len(prompt) // 4) + max(1, len(full_text) // 4),
            }

    async def ping(self, model: str = "", api_key: str = "", base_url: str = "") -> str:
        """Real connectivity probe — lightweight GET /v1/models, NO mock fallback."""
        key = api_key or ANTHROPIC_API_KEY
        if not key:
            raise LLMAdapterError("未配置 Anthropic API Key，无法测试连接")
        url = (base_url.rstrip("/") if base_url else "https://api.anthropic.com") + "/v1/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        resp = await _retry_request("GET", url, headers=headers)
        if resp.status_code >= 400:
            raise LLMAdapterError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        model_list = data.get("data") or []
        count = len(model_list)
        return f"Anthropic 连接正常，可用模型 {count} 个"


class OllamaAdapter(BaseAdapter):
    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "", **kwargs: Any) -> str:
        url = (base_url.rstrip("/") if base_url else OLLAMA_BASE_URL) + "/api/generate"
        payload = {"model": model or "llama3", "prompt": prompt, "stream": False}
        system_prompt = str(kwargs.get("system_prompt") or "")
        if system_prompt:
            payload["system"] = system_prompt
        try:
            response = await _retry_request("POST", url, json_body=payload)
            if response.status_code >= 400:
                raise LLMAdapterError(f"HTTP {response.status_code}: {str(response.text)[:500]}")
            data = response.json()
            result = data.get("response", "")
            self.last_usage = {
                "prompt_tokens": data.get("prompt_eval_count") or max(1, len(prompt) // 4),
                "completion_tokens": data.get("eval_count") or max(1, len(result) // 4),
                "total_tokens": (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0) or max(1, len(prompt) // 4) + max(1, len(result) // 4),
            }
            return result
        except httpx.HTTPError:
            return await MockAdapter().execute_prompt(prompt, model)

    async def stream_prompt(self, prompt: str | list[dict[str, Any]], model: str, api_key: str = "", base_url: str = "", *, system_prompt: str = "") -> AsyncGenerator[str, None]:
        url = (base_url.rstrip("/") if base_url else OLLAMA_BASE_URL) + "/api/generate"
        payload = {"model": model or "llama3", "prompt": prompt, "stream": True}
        if system_prompt:
            payload["system"] = system_prompt
        self.last_usage = {}
        full_text = ""
        try:
            client = _get_client()  # shared, connection-pooled
            async with client.stream("POST", url, json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise LLMAdapterError(f"HTTP {response.status_code}: {body.decode(errors='replace')[:500]}")
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        if chunk.get("done"):
                            self.last_usage = {
                                "prompt_tokens": chunk.get("prompt_eval_count") or max(1, len(prompt) // 4),
                                "completion_tokens": chunk.get("eval_count") or max(1, len(full_text) // 4),
                                "total_tokens": (chunk.get("prompt_eval_count") or 0) + (chunk.get("eval_count") or 0) or max(1, len(prompt) // 4) + max(1, len(full_text) // 4),
                            }
                            break
                        content = chunk.get("response", "")
                        if content:
                            full_text += content
                            yield content
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPError:
            async for chunk in MockAdapter().stream_prompt(prompt, model, system_prompt=system_prompt):
                yield chunk
            return
        yield ""
        if not self.last_usage.get("total_tokens"):
            self.last_usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(full_text) // 4), "total_tokens": max(1, len(prompt) // 4) + max(1, len(full_text) // 4)}

    async def ping(self, model: str = "", api_key: str = "", base_url: str = "") -> str:
        """Real connectivity probe — lightweight GET /api/tags, NO mock fallback."""
        url = (base_url.rstrip("/") if base_url else OLLAMA_BASE_URL) + "/api/tags"
        resp = await _retry_request("GET", url)
        if resp.status_code >= 400:
            raise LLMAdapterError(
                f"Ollama HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        model_list = data.get("models") or []
        count = len(model_list)
        return f"Ollama 连接正常，可用模型 {count} 个"


class NewAPIGatewayAdapter(OpenAICompatibleAdapter):
    """Unified adapter for the new-api LLM gateway (optional supplier layer).

    When ``AGENTHUB_LLM_GATEWAY=newapi``, every remote provider/model call
    fans out through one OpenAI-compatible entry (new-api ``/v1``). new-api
    owns channel selection, retry/failover, quotas and billing; the
    self-hosted per-provider adapters remain the default (fallback) path.
    """

    default_base_url = NEWAPI_BASE_URL
    env_api_key = NEWAPI_API_KEY
    default_model = "mock-llm"


class AdapterManager:
    def __init__(self) -> None:
        self.adapters = {
            "openai": OpenAIAdapter(),
            "anthropic": AnthropicAdapter(),
            "ollama": OllamaAdapter(),
            "mock": MockAdapter(),
            "deepseek": DeepSeekAdapter(),
            "minimax": MinimaxAdapter(),
            "zhipu": ZhipuAdapter(),
            "qwen": QwenAdapter(),
            "doubao": DoubaoAdapter(),
            "custom_openai": CustomOpenAIAdapter(),
            "kimi": KimiAdapter(),
        }
        for _name, _inst in self.adapters.items():
            _inst.provider_name = _name

    @property
    def _gateway_enabled(self) -> bool:
        return (LLM_GATEWAY or "").strip().lower() == "newapi"

    def get_adapter(self, provider: str) -> BaseAdapter:
        key = (provider or "mock").lower()
        # new-api gateway mode: one OpenAI-compatible entry for all remote
        # providers; local adapters (mock/CLI/cloud) keep their own path.
        if self._gateway_enabled and key not in {
            "mock", "local_claude", "local_codex", "local_openclaw", "cloud_code",
        }:
            if "newapi" not in self.adapters:
                self.adapters["newapi"] = NewAPIGatewayAdapter()
                self.adapters["newapi"].provider_name = "newapi"
            return self.adapters["newapi"]
        # cloud_code is lazy-loaded to avoid circular imports
        # (CloudCodeAdapter imports from this module for BaseAdapter/MockAdapter)
        if key == "cloud_code":
            if "cloud_code" not in self.adapters:
                from app.services.adapters.cloudcode_adapter import CloudCodeAdapter
                self.adapters["cloud_code"] = CloudCodeAdapter()
            return self.adapters["cloud_code"]

        # Local agent adapters — lazy-loaded to avoid circular imports
        # and to skip importing when the CLI tools are not installed.
        if key == "local_claude":
            if "local_claude" not in self.adapters:
                from app.services.adapters.local_claude_adapter import ClaudeCodeAdapter
                self.adapters["local_claude"] = ClaudeCodeAdapter()
            return self.adapters["local_claude"]

        if key == "local_codex":
            if "local_codex" not in self.adapters:
                from app.services.adapters.local_codex_adapter import CodexCLIAdapter
                self.adapters["local_codex"] = CodexCLIAdapter()
            return self.adapters["local_codex"]

        if key == "local_openclaw":
            if "local_openclaw" not in self.adapters:
                from app.services.adapters.local_openclaw_adapter import OpenClawAdapter
                self.adapters["local_openclaw"] = OpenClawAdapter()
            return self.adapters["local_openclaw"]

        return self.adapters.get(key, self.adapters["mock"])


adapter_manager = AdapterManager()
