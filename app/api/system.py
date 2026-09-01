from __future__ import annotations

from fastapi import APIRouter

from app.services.adapter_manager import adapter_manager

router = APIRouter(prefix="/api", tags=["system"])

# 适配器类型元数据
ADAPTER_METADATA = {
    "openai": {
        "name": "OpenAI",
        "description": "OpenAI 官方 API，支持 GPT-3.5/4 系列模型",
        "default_model": "gpt-3.5-turbo",
        "default_base_url": "https://api.openai.com/v1",
        "requires_api_key": True,
        "category": "cloud",
    },
    "anthropic": {
        "name": "Anthropic",
        "description": "Anthropic Claude 系列模型",
        "default_model": "claude-3-sonnet",
        "default_base_url": "https://api.anthropic.com",
        "requires_api_key": True,
        "category": "cloud",
    },
    "ollama": {
        "name": "Ollama",
        "description": "本地部署的开源模型，需提前安装 Ollama",
        "default_model": "llama3",
        "default_base_url": "http://localhost:11434",
        "requires_api_key": False,
        "category": "local",
    },
    "mock": {
        "name": "本地模拟",
        "description": "本地 Mock 模式，无需 API Key，适合开发测试",
        "default_model": "mock",
        "default_base_url": "",
        "requires_api_key": False,
        "category": "mock",
    },
    "deepseek": {
        "name": "DeepSeek",
        "description": "深度求索 DeepSeek-V4 系列模型",
        "default_model": "deepseek-v4-flash",
        "default_base_url": "https://api.deepseek.com/v1",
        "requires_api_key": True,
        "category": "cloud",
    },
    "minimax": {
        "name": "MiniMax",
        "description": "MiniMax abab 系列模型",
        "default_model": "abab6-chat",
        "default_base_url": "https://api.minimax.chat/v1",
        "requires_api_key": True,
        "category": "cloud",
    },
    "zhipu": {
        "name": "智谱 AI",
        "description": "智谱 GLM-4 系列模型",
        "default_model": "glm-4",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "requires_api_key": True,
        "category": "cloud",
    },
    "qwen": {
        "name": "通义千问",
        "description": "阿里通义千问 Qwen 系列模型",
        "default_model": "qwen-turbo",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "requires_api_key": True,
        "category": "cloud",
    },
    "doubao": {
        "name": "字节豆包",
        "description": "字节跳动豆包系列模型",
        "default_model": "Doubao-3.5",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "requires_api_key": True,
        "category": "cloud",
    },
    "kimi": {
        "name": "Moonshot Kimi",
        "description": "月之暗面 Kimi，支持超长上下文",
        "default_model": "kimi-k2.6",
        "default_base_url": "https://api.moonshot.cn/v1",
        "requires_api_key": True,
        "category": "cloud",
    },
    "custom_openai": {
        "name": "自定义 OpenAI 兼容",
        "description": "自定义 OpenAI 兼容接口，适用于本地部署的 llama.cpp 等",
        "default_model": "",
        "default_base_url": "",
        "requires_api_key": True,
        "category": "custom",
    },
    "local_claude": {
        "name": "Claude Code (本地)",
        "description": "Anthropic 官方 CLI Agent，通过 subprocess 无头模式调用本地 claude 命令",
        "default_model": "claude",
        "default_base_url": "",
        "requires_api_key": False,
        "category": "local",
    },
    "local_codex": {
        "name": "Codex CLI (本地)",
        "description": "OpenAI Codex CLI，通过 codex exec --json 执行本地编码任务",
        "default_model": "codex",
        "default_base_url": "",
        "requires_api_key": False,
        "category": "local",
    },
    "local_openclaw": {
        "name": "OpenClaw (本地)",
        "description": "开源 TypeScript CLI Agent，支持 MCP 和多后端切换",
        "default_model": "openclaw-cli",
        "default_base_url": "",
        "requires_api_key": False,
        "category": "local",
    },
}


@router.get("/preview/{task_id}")
async def preview(task_id: str) -> dict[str, str]:
    return {"taskId": task_id, "url": "http://localhost:3000"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "AgentHub"}


@router.get("/adapters")
async def list_adapters() -> dict:
    """获取所有可用的适配器类型及其详细信息"""
    adapters = []
    for provider_id, adapter in adapter_manager.adapters.items():
        metadata = ADAPTER_METADATA.get(provider_id, {})
        adapters.append({
            "id": provider_id,
            "name": metadata.get("name", provider_id),
            "description": metadata.get("description", ""),
            "default_model": metadata.get("default_model", ""),
            "default_base_url": metadata.get("default_base_url", ""),
            "requires_api_key": metadata.get("requires_api_key", True),
            "category": metadata.get("category", "cloud"),
        })
    return {"adapters": adapters, "total": len(adapters)}


@router.get("/adapters/{adapter_id}")
async def get_adapter(adapter_id: str) -> dict:
    """获取指定适配器的详细信息"""
    metadata = ADAPTER_METADATA.get(adapter_id)
    if not metadata:
        return {"error": f"未知的适配器类型: {adapter_id}"}
    return {
        "id": adapter_id,
        "name": metadata.get("name", adapter_id),
        "description": metadata.get("description", ""),
        "default_model": metadata.get("default_model", ""),
        "default_base_url": metadata.get("default_base_url", ""),
        "requires_api_key": metadata.get("requires_api_key", True),
        "category": metadata.get("category", "cloud"),
    }


# ═══════════════════════════════════════════════════════════════════════
# Performance Monitoring API
# ═══════════════════════════════════════════════════════════════════════


@router.get("/metrics")
async def get_metrics() -> dict:
    """Return full performance metrics snapshot.

    Includes per-model latency (avg/p50/p95/p99), success rates,
    streaming performance (TTFT, chunk gaps), WebSocket broadcast
    timing, and recent degradation events.
    """
    from app.services.performance_monitor import monitor
    return monitor.snapshot()


@router.get("/metrics/health")
async def get_metrics_health() -> dict:
    """Lightweight health check — model status, active degradations, retries."""
    from app.services.performance_monitor import monitor
    return monitor.model_health()
