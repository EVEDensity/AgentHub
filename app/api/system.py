from __future__ import annotations

from fastapi import APIRouter

from app.services.adapter_manager import adapter_manager
from app.services.langgraph_workflow import agent_workflow

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
}


@router.get("/preview/{task_id}")
async def preview(task_id: str) -> dict[str, str]:
    return {"taskId": task_id, "url": "http://localhost:3000"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "AgentHub"}


@router.get("/langgraph/graph")
async def get_langgraph_graph() -> dict:
    """获取 LangGraph 工作流的可视化信息"""
    return agent_workflow.get_graph_visualization()


@router.get("/langgraph/graph/ascii")
async def get_langgraph_ascii() -> dict[str, str]:
    """获取 LangGraph 工作流的 ASCII 图形"""
    return {"ascii_diagram": agent_workflow.get_ascii_diagram()}


@router.get("/langgraph/nodes")
async def get_langgraph_nodes() -> dict:
    """获取 LangGraph 工作流的节点信息"""
    return {"nodes": agent_workflow._graph_info["nodes"]}


@router.get("/langgraph/edges")
async def get_langgraph_edges() -> dict:
    """获取 LangGraph 工作流的边信息"""
    return {"edges": agent_workflow._graph_info["edges"]}


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
