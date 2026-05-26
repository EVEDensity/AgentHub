from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.config import ANTHROPIC_API_KEY, ENABLE_REAL_LLM, OLLAMA_BASE_URL, OPENAI_API_KEY, REQUEST_TIMEOUT_SECONDS


class LLMAdapterError(RuntimeError):
    pass


class BaseAdapter:
    def __init__(self) -> None:
        self.last_usage: dict[str, int] = {}

    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> str:
        raise NotImplementedError

    async def stream_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> AsyncGenerator[str, None]:
        result = await self.execute_prompt(prompt, model, api_key, base_url)
        chunk_size = max(1, len(result) // 20)
        for i in range(0, len(result), chunk_size):
            yield result[i:i + chunk_size]
        yield ""


class MockAdapter(BaseAdapter):
    async def execute_prompt(self, prompt: str, model: str = "mock", api_key: str = "", base_url: str = "") -> str:
        result = f"本地 Mock 模型响应：{prompt[:500]}"
        self.last_usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(result) // 4), "total_tokens": max(1, len(prompt) // 4) + max(1, len(result) // 4)}
        return result


class OpenAICompatibleAdapter(BaseAdapter):
    default_base_url = "https://api.openai.com/v1"
    env_api_key = OPENAI_API_KEY
    default_model = "gpt-3.5-turbo"

    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> str:
        key = api_key or self.env_api_key
        if not ENABLE_REAL_LLM or not key:
            return await MockAdapter().execute_prompt(prompt, model)
        actual_model = model if model != "ping" else self.default_model
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "/chat/completions"
        payload: dict[str, Any] = {"model": actual_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
        if response.status_code >= 400:
            raise LLMAdapterError(response.text)
        data = response.json()
        usage = data.get("usage", {})
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens") or max(1, len(prompt) // 4),
            "completion_tokens": usage.get("completion_tokens") or max(1, len(data["choices"][0]["message"]["content"]) // 4),
            "total_tokens": usage.get("total_tokens") or (self.last_usage.get("prompt_tokens", 0) + self.last_usage.get("completion_tokens", 0)),
        }
        return data["choices"][0]["message"]["content"]

    async def stream_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> AsyncGenerator[str, None]:
        key = api_key or self.env_api_key
        if not ENABLE_REAL_LLM or not key:
            async for chunk in MockAdapter().stream_prompt(prompt, model):
                yield chunk
            return
        actual_model = model if model != "ping" else self.default_model
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "//chat/completions"
        url = url.replace("//chat", "/chat")  # normalize double slash
        payload: dict[str, Any] = {"model": actual_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2, "stream": True, "stream_options": {"include_usage": True}}
        full_text = ""
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
                        content = delta.get("content", "")
                        if content:
                            full_text += content
                            yield content
                        # 捕获 usage（部分 API 在最后 chunk 返回）
                        if "usage" in obj:
                            u = obj["usage"]
                            self.last_usage = {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0), "total_tokens": u.get("total_tokens", 0)}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        yield ""
        # 如果没有从 chunk 中拿到 usage，使用估算
        if not self.last_usage:
            self.last_usage = {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": max(1, len(full_text) // 4), "total_tokens": max(1, len(prompt) // 4) + max(1, len(full_text) // 4)}


class OpenAIAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.openai.com/v1"


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


class AnthropicAdapter(BaseAdapter):
    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> str:
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
        return "\n".join(block.get("text", "") for block in response.json().get("content", []) if block.get("type") == "text")


class OllamaAdapter(BaseAdapter):
    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> str:
        url = (base_url.rstrip("/") if base_url else OLLAMA_BASE_URL) + "/api/generate"
        payload = {"model": model or "llama3", "prompt": prompt, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload)
            if response.status_code >= 400:
                raise LLMAdapterError(response.text)
            return response.json().get("response", "")
        except httpx.HTTPError:
            return await MockAdapter().execute_prompt(prompt, model)

    async def stream_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> AsyncGenerator[str, None]:
        url = (base_url.rstrip("/") if base_url else OLLAMA_BASE_URL) + "/api/generate"
        payload = {"model": model or "llama3", "prompt": prompt, "stream": True}
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
                                break
                            content = chunk.get("response", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except httpx.HTTPError:
            async for chunk in MockAdapter().stream_prompt(prompt, model):
                yield chunk
            return
        yield ""


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
        }

    def get_adapter(self, provider: str) -> BaseAdapter:
        return self.adapters.get((provider or "mock").lower(), self.adapters["mock"])


adapter_manager = AdapterManager()
