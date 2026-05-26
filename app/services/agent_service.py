from __future__ import annotations

import json
import random
import re
import time
import uuid

from app.db.init_db import now
from app.db.session import dict_rows, get_connection, one_row
from app.services.adapter_manager import adapter_manager
from app.services.auth.service import AuthService
from app.services.codegen_service import write_generated_files
from app.services.secret_service import decrypt_secret
from app.services.symbolic import generate_symbolic_message, public_symbolic

AGENTS = {"Orchestrator", "Architect", "CodeGen", "Review", "Test", "Deploy"}
_RUNTIME: dict[str, dict] = {}


def extract_mentions(content: str) -> list[str]:
    return re.findall(r"@(\w+)", content)


def resolve_agent(content: str) -> dict:
    target = next((name for name in extract_mentions(content) if name in AGENTS), "Orchestrator")
    agent = one_row("SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=?", (target,))
    return agent or {"agent_id": "Orchestrator", "domain": "orchestrator", "adapter_type": "mock", "risk_level": "L2"}


def candidate_models_for_role(role: str) -> list[dict]:
    rows = dict_rows(
        "SELECT mc.id,mc.provider,mc.model_name AS model_name,mc.api_key,mc.base_url,rb.prompt FROM role_bindings rb JOIN model_configs mc ON rb.model_config_id=mc.id WHERE rb.role=? AND mc.is_active=1 ORDER BY mc.id DESC",
        (role,),
    )
    if rows:
        return rows
    rows = dict_rows("SELECT id,provider,model_name,api_key,base_url,'' AS prompt FROM model_configs WHERE is_active=1 ORDER BY id DESC")
    return rows or [{"id": 0, "provider": "mock", "model_name": "mock", "api_key": "", "base_url": "", "prompt": ""}]


def _score(model: dict) -> float:
    key = f"{model.get('provider')}:{model.get('model_name')}:{model.get('base_url','')}"
    s = _RUNTIME.get(key, {"ok": 0, "fail": 0, "latency": 1200.0})
    total = max(1, s["ok"] + s["fail"])
    success = s["ok"] / total
    latency_score = max(0.05, min(1.0, 1000.0 / max(80.0, s["latency"])))
    return 0.65 * success + 0.35 * latency_score + random.uniform(0.0, 0.05)


def choose_models(models: list[dict]) -> list[dict]:
    ranked = sorted(models, key=_score, reverse=True)
    return ranked


def _update_runtime(model: dict, ok: bool, latency_ms: float) -> None:
    key = f"{model.get('provider')}:{model.get('model_name')}:{model.get('base_url','')}"
    state = _RUNTIME.setdefault(key, {"ok": 0, "fail": 0, "latency": latency_ms})
    if ok:
        state["ok"] += 1
    else:
        state["fail"] += 1
    state["latency"] = state["latency"] * 0.7 + latency_ms * 0.3


def save_message(
    session_id: str,
    sender: str,
    content: str,
    msg_type: str,
    score: float = 0.95,
    symbolic: dict | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages(id,session_id,sender,content,type,fidelity_score,symbolic_json,prompt_tokens,completion_tokens,total_tokens,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                session_id,
                sender,
                content,
                msg_type,
                score,
                json.dumps(symbolic or {}, ensure_ascii=False),
                prompt_tokens,
                completion_tokens,
                total_tokens,
                now(),
            ),
        )


def list_messages(session_id: str) -> list[dict]:
    items = dict_rows(
        "SELECT session_id AS sessionId,sender,content,type,fidelity_score AS fidelityScore,symbolic_json,created_at AS timestamp FROM messages WHERE session_id=? ORDER BY created_at",
        (session_id,),
    )
    for item in items:
        item["symbolic"] = json.loads(item.pop("symbolic_json") or "{}")
    return items


async def call_agent(session_id: str, content: str, user_id: str) -> dict:
    agent = resolve_agent(content)
    domain = agent["domain"]
    msg_type = "code" if domain == "codegen" or any(word in content.lower() for word in ["code", "fastapi", "react", "代码", "实现"]) else "text"
    symbolic = generate_symbolic_message(content, msg_type, session_id)
    models = choose_models(candidate_models_for_role(agent["agent_id"]))
    prompt = build_prompt(agent["agent_id"], domain, content, symbolic, models[0].get("prompt", "") if models else "")

    result = ""
    selected = models[0] if models else {"provider": "mock", "model_name": "mock", "api_key": "", "base_url": ""}
    errors: list[str] = []
    for model in models:
        selected = model
        adapter = adapter_manager.get_adapter(model.get("provider", "mock"))
        started = time.perf_counter()
        try:
            result = await adapter.execute_prompt(prompt, model.get("model_name", "mock"), decrypt_secret(model.get("api_key", "")), model.get("base_url", ""))
            _update_runtime(model, True, (time.perf_counter() - started) * 1000)
            break
        except Exception as exc:
            _update_runtime(model, False, (time.perf_counter() - started) * 1000)
            errors.append(f"{model.get('provider')}/{model.get('model_name')}: {exc}")
            result = ""
    if not result:
        result = "模型调用失败，已降级为本地响应：" + " | ".join(errors[:2])

    content_out = normalize_agent_output(agent["agent_id"], result, content)
    prompt_tokens, completion_tokens, total_tokens = _estimate_token_usage(content, content_out)
    generated = write_generated_files(content_out, content) if agent["agent_id"] == "CodeGen" else None
    public = {**public_symbolic(symbolic), "generated": generated, "model": {"provider": selected.get("provider"), "modelName": selected.get("model_name")}}
    write_audit(user_id, agent["agent_id"], "agent_execute", agent.get("risk_level", "L1"), "auto", {"sessionId": session_id, "domain": domain, "generated": generated, "model": public["model"], "fallbackErrors": errors[:3]})
    display_content = "CodeGen 已生成结构化文件，请在下方生成文件面板中检查内容、查看 Diff，并确认提交。" if generated else content_out
    message = {
        "event": "message",
        "sessionId": session_id,
        "content": display_content,
        "sender": agent["agent_id"],
        "timestamp": now(),
        "type": "code" if agent["agent_id"] == "CodeGen" else "text",
        "fidelityScore": symbolic["fidelity_score"],
        "symbolic": public,
    }
    save_message(
        session_id,
        message["sender"],
        message["content"],
        message["type"],
        message["fidelityScore"],
        message["symbolic"],
        prompt_tokens,
        completion_tokens,
        total_tokens,
    )
    return message


def build_prompt(agent_id: str, domain: str, content: str, symbolic: dict, role_prompt: str) -> str:
    base = role_prompt or "你是 AgentHub 多智能体平台中的领域 Agent，必须输出清晰、可执行、可审计的结果。"
    if agent_id == "CodeGen":
        return (
            f"{base}\n"
            "你是 CodeGenAgent。必须只输出 JSON，不要 Markdown，不要解释。JSON 格式："
            "{\"files\":[{\"path\":\"backend/example.py\",\"content\":\"文件完整内容\"}]}。"
            "路径只能是相对路径，代码必须完整可运行。\n"
            f"符号消息: {json.dumps(public_symbolic(symbolic), ensure_ascii=False)}\n用户需求: {content}"
        )
    return f"{base}\nAgent: {agent_id}\nDomain: {domain}\n符号消息: {json.dumps(public_symbolic(symbolic), ensure_ascii=False)}\n用户需求: {content}"


def _estimate_token_usage(user_text: str, model_output: str) -> tuple[int, int, int]:
    prompt_tokens = max(1, len(user_text) // 4)
    completion_tokens = max(1, len(model_output) // 4)
    total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def normalize_agent_output(agent_id: str, model_output: str, original: str) -> str:
    if agent_id == "CodeGen":
        if model_output and not model_output.startswith("本地 Mock 模型响应") and not model_output.startswith("模型调用失败"):
            return model_output
        return json.dumps(
            {
                "files": [
                    {
                        "path": "backend/health_router.py" if "fastapi" in original.lower() or "路由" in original else "frontend/GeneratedPanel.jsx",
                        "content": "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/generated\", tags=[\"generated\"])\n\n\n@router.get(\"/health\")\nasync def generated_health() -> dict[str, str]:\n    return {\"status\": \"ok\", \"module\": \"agenthub-generated\"}\n",
                    }
                ]
            },
            ensure_ascii=False,
        )
    if model_output and not model_output.startswith("本地 Mock 模型响应"):
        return model_output
    if agent_id == "Review":
        return "Review 完成：结构符合 FastAPI + Next.js 分层方案，建议生产环境收紧 CORS、加入鉴权、限流和审计。"
    if agent_id == "Test":
        return "Test 完成：请验证 /api/health、/api/admin/model-config、/ws/session-1、DAG 状态机和 Git 接口。"
    if agent_id == "Deploy":
        return "Deploy 准备完成：前端 http://localhost:3000，后端 http://localhost:8000。高风险发布需管理员确认。"
    return f"已进入元调度：{original[:80]}。DAG 已生成，并按需激活领域 Agent。"
