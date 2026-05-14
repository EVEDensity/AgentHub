from __future__ import annotations

from typing import Any

import httpx

from app.config import ANTHROPIC_API_KEY, ENABLE_REAL_LLM, OLLAMA_BASE_URL, OPENAI_API_KEY, REQUEST_TIMEOUT_SECONDS


class LLMAdapterError(RuntimeError):
    pass


class BaseAdapter:
    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> str:
        raise NotImplementedError


class MockAdapter(BaseAdapter):
    async def execute_prompt(self, prompt: str, model: str = "mock", api_key: str = "", base_url: str = "") -> str:
        return f"本地 Mock 模型响应：{prompt[:500]}"


class OpenAICompatibleAdapter(BaseAdapter):
    default_base_url = "https://api.openai.com/v1"
    env_api_key = OPENAI_API_KEY

    async def execute_prompt(self, prompt: str, model: str, api_key: str = "", base_url: str = "") -> str:
        key = api_key or self.env_api_key
        if not ENABLE_REAL_LLM or not key:
            return await MockAdapter().execute_prompt(prompt, model)
        url = (base_url.rstrip("/") if base_url else self.default_base_url) + "/chat/completions"
        payload: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
        if response.status_code >= 400:
            raise LLMAdapterError(response.text)
        return response.json()["choices"][0]["message"]["content"]


class OpenAIAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.openai.com/v1"


class DeepSeekAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.deepseek.com/v1"


class MinimaxAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://api.minimax.chat/v1"


class ZhipuAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://open.bigmodel.cn/api/paas/v4"


class QwenAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class DoubaoAdapter(OpenAICompatibleAdapter):
    default_base_url = "https://ark.cn-beijing.volces.com/api/v3"


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
