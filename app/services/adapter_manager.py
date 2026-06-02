from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.config import ANTHROPIC_API_KEY, ENABLE_REAL_LLM, OLLAMA_BASE_URL, OPENAI_API_KEY, REQUEST_TIMEOUT_SECONDS

logger = logging.getLogger("agenthub.adapter")


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
    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {}

    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "", **kwargs: Any) -> str:
        raise NotImplementedError

    async def stream_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> AsyncGenerator[str, None]:
        result = await self.execute_prompt(prompt, model, api_key, base_url)
        chunk_size = max(1, len(result) // 20)
        for i in range(0, len(result), chunk_size):
            yield result[i:i + chunk_size]
            await asyncio.sleep(0)
        yield ""


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

    async def stream_prompt(self, prompt: str, model: str = "mock", api_key: str = "", base_url: str = "") -> AsyncGenerator[str, None]:
        """Mock streaming: delegate to execute_prompt and yield the full result."""
        result = await self.execute_prompt(prompt, model, api_key, base_url)
        # Yield the full result as one chunk so tool-call JSON is complete
        yield result
        yield ""  # end-of-stream marker

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
                if line.startswith('- 分类:'):
                    section = 'meta'
                elif line.startswith('- 风险等级:'):
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

    async def execute_prompt(
        self, prompt: str, model: str, api_key: str = "", base_url: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Execute a prompt, optionally with native function-calling tools.

        When *tools* is a non-empty list of OpenAI-format tool definitions,
        they are passed as the ``tools`` request parameter so the model can
        natively return ``tool_calls`` instead of plain text.

        Native ``tool_calls`` in the response are transparently converted to
        our internal ``{"tool_calls": [...]}`` JSON string so the existing
        parsing pipeline handles them without modification.
        """
        key = api_key or self.env_api_key
        if not ENABLE_REAL_LLM or not key:
            return await MockAdapter().execute_prompt(prompt, model)
        actual_model = model if model != "ping" else self.default_model
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "/chat/completions"
        payload: dict[str, Any] = {
            "model": actual_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "max_tokens": 4096,  # generous for reasoning models (DeepSeek V4, Kimi K2)
        }
        # ── Native function calling ──────────────────────────────────
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # ── Execute ──────────────────────────────────────────────────
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
        if response.status_code >= 400:
            raise LLMAdapterError(response.text)
        data = response.json()
        usage = data.get("usage", {})
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens") or max(1, len(prompt) // 4),
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
        }

        message = data["choices"][0]["message"]
        finish_reason = data["choices"][0].get("finish_reason", "")

        # ── Native tool_calls → internal JSON format ─────────────────
        native_tool_calls = message.get("tool_calls") or []
        if native_tool_calls:
            converted: list[dict[str, Any]] = []
            for tc in native_tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                args_str = func.get("arguments", "{}")
                try:
                    arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
                except (json.JSONDecodeError, TypeError):
                    arguments = {}
                if name:
                    converted.append({"name": name, "arguments": arguments})
            # Return in the same format our prompt-based parser expects
            return json.dumps({"tool_calls": converted}, ensure_ascii=False)

        # ── Plain text response ──────────────────────────────────────
        # Reasoning models (DeepSeek V4, Kimi K2, etc.) separate thinking
        # from the final answer: reasoning_content holds the chain-of-thought
        # and content may be empty if the model stopped early (e.g. hit
        # token limit during reasoning).  We combine both so the frontend
        # always has text to display, and wrap reasoning in <think> tags
        # so ThinkingPanel renders it properly.
        content = message.get("content") or ""
        reasoning = message.get("reasoning_content") or ""

        if reasoning and not content:
            # Model only output reasoning — likely hit token limit.
            # Return the reasoning wrapped so the user sees something.
            content = f"<think>{reasoning}</think>\n\n[模型推理阶段完成，但未生成最终回复 — 请重试或简化问题]"
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

    async def stream_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> AsyncGenerator[str, None]:
        key = api_key or self.env_api_key
        if not ENABLE_REAL_LLM or not key:
            async for chunk in MockAdapter().stream_prompt(prompt, model):
                yield chunk
            return
        actual_model = model if model != "ping" else self.default_model
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "//chat/completions"
        url = url.replace("//chat", "/chat")  # normalize double slash
        payload: dict[str, Any] = {"model": actual_model, "messages": [{"role": "user", "content": prompt}], "temperature": self.temperature, "stream": True, "frequency_penalty": self.frequency_penalty, "presence_penalty": self.presence_penalty}
        if self.supports_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        self.last_usage = {}  # reset per call so stale data never leaks
        full_text = ""
        reasoning_open = False
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            async with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise LLMAdapterError(body.decode(errors="replace"))
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0].get("delta", {})
                        reasoning = delta.get("reasoning_content", "")
                        content = delta.get("content", "")
                        if reasoning:
                            if not reasoning_open:
                                reasoning_open = True
                                full_text += "<think>"
                                yield "<think>"
                            full_text += reasoning
                            yield reasoning
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
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                if reasoning_open:
                    full_text += "</think>"
                    yield "</think>"
        yield ""
        # Always set fallback estimation when no real usage was captured from chunks
        if not self.last_usage.get("total_tokens"):
            self.last_usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(full_text) // 4), "total_tokens": max(1, len(prompt) // 4) + max(1, len(full_text) // 4)}


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
    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "", **kwargs: Any) -> str:
        key = api_key or ANTHROPIC_API_KEY
        if not ENABLE_REAL_LLM or not key:
            return await MockAdapter().execute_prompt(prompt, model)
        url = (base_url.rstrip("/") if base_url else "https://api.anthropic.com") + "/v1/messages"
        payload = {"model": model, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise LLMAdapterError(response.text)
        data = response.json()
        usage = data.get("usage", {})
        self.last_usage = {
            "prompt_tokens": usage.get("input_tokens") or max(1, len(prompt) // 4),
            "completion_tokens": usage.get("output_tokens") or max(1, len(data.get("content", [{}])[0].get("text", "")) // 4),
            "total_tokens": (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0) or max(1, len(prompt) // 4) + max(1, len(data.get("content", [{}])[0].get("text", "")) // 4),
        }
        return "\n".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")


class OllamaAdapter(BaseAdapter):
    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "", **kwargs: Any) -> str:
        url = (base_url.rstrip("/") if base_url else OLLAMA_BASE_URL) + "/api/generate"
        payload = {"model": model or "llama3", "prompt": prompt, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload)
            if response.status_code >= 400:
                raise LLMAdapterError(response.text)
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

    async def stream_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> AsyncGenerator[str, None]:
        url = (base_url.rstrip("/") if base_url else OLLAMA_BASE_URL) + "/api/generate"
        payload = {"model": model or "llama3", "prompt": prompt, "stream": True}
        self.last_usage = {}
        full_text = ""
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise LLMAdapterError(body.decode(errors="replace"))
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
            async for chunk in MockAdapter().stream_prompt(prompt, model):
                yield chunk
            return
        yield ""
        if not self.last_usage.get("total_tokens"):
            self.last_usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(full_text) // 4), "total_tokens": max(1, len(prompt) // 4) + max(1, len(full_text) // 4)}


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

    def get_adapter(self, provider: str) -> BaseAdapter:
        return self.adapters.get((provider or "mock").lower(), self.adapters["mock"])


adapter_manager = AdapterManager()
