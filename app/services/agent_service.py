from __future__ import annotations

import json
import random
import re
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.db.init_db import now
from app.db.session import dict_rows, get_connection, one_row
from app.services.adapter_manager import adapter_manager
from app.services.auth.service import AuthService
from app.services.codegen_service import write_generated_files
from app.services.secret_service import decrypt_secret
from app.services.symbolic import generate_symbolic_message, public_symbolic

AGENTS = {"Orchestrator", "Architect", "CodeGen", "Review", "Test", "Deploy"}
_RUNTIME: dict[str, dict] = {}


def _build_attachment_context(attachments: list[dict[str, Any]] | None) -> tuple[str, list[dict[str, Any]]]:
    if not attachments:
        return "", []

    blocks: list[str] = []
    clean: list[dict[str, Any]] = []
    max_text_len = 12000

    for idx, item in enumerate(attachments, start=1):
        name = str(item.get("name", f"file_{idx}"))
        file_type = str(item.get("type", "text/plain"))
        size = int(item.get("size", 0) or 0)
        content = str(item.get("content", ""))

        is_image = file_type.startswith("image/") or name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"))
        if is_image:
            preview = content[:180]
            blocks.append(
                f"[附件图片 {idx}] name={name}, type={file_type}, size={size}\\n"
                f"data_url_prefix={preview}"
            )
        else:
            trimmed = content[:max_text_len]
            ext = name.split(".")[-1] if "." in name else "text"
            blocks.append(
                f"[附件文件 {idx}] name={name}, type={file_type}, size={size}\\n"
                f"```{ext}\\n{trimmed}\\n```"
            )

        clean.append({"name": name, "type": file_type, "size": size})

    return "\\n\\n".join(blocks), clean


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
        "SELECT id,session_id AS sessionId,sender,content,type,fidelity_score AS fidelityScore,symbolic_json,created_at AS timestamp FROM messages WHERE session_id=? ORDER BY created_at",
        (session_id,),
    )
    for item in items:
        item["symbolic"] = json.loads(item.pop("symbolic_json") or "{}")
    return items


async def call_agent(session_id: str, content: str, user_id: str, attachments: list[dict[str, Any]] | None = None) -> dict:
    agent = resolve_agent(content)
    domain = agent["domain"]
    msg_type = "code" if domain == "codegen" or any(word in content.lower() for word in ["code", "fastapi", "react", "代码", "实现"]) else "text"

    attachment_context, attachment_meta = _build_attachment_context(attachments)
    llm_input = content
    if attachment_context:
        llm_input = f"{content}\n\n[用户上传附件上下文]\n{attachment_context}"

    symbolic = generate_symbolic_message(llm_input, msg_type, session_id)
    models = choose_models(candidate_models_for_role(agent["agent_id"]))
    prompt = build_prompt(agent["agent_id"], domain, llm_input, symbolic, models[0].get("prompt", "") if models else "")

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
    usage = adapter.last_usage
    if usage and usage.get("total_tokens", 0) > 0:
        prompt_tokens, completion_tokens, total_tokens = usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]
    else:
        prompt_tokens, completion_tokens, total_tokens = _estimate_token_usage(content, content_out)
    generated = write_generated_files(content_out, content) if agent["agent_id"] == "CodeGen" else None
    public = {
        **public_symbolic(symbolic),
        "generated": generated,
        "model": {"provider": selected.get("provider"), "modelName": selected.get("model_name")},
        "attachments": attachment_meta,
    }
    AuthService.write_audit(user_id, agent["agent_id"], "agent_execute", agent.get("risk_level", "L1"), "auto", {"sessionId": session_id, "domain": domain, "generated": generated, "model": public["model"], "fallbackErrors": errors[:3]})
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


async def stream_agent_response(
    session_id: str,
    content: str,
    user_id: str,
    token=None,
    attachments: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[str, None] | None:
    agent = resolve_agent(content)
    models = choose_models(candidate_models_for_role(agent["agent_id"]))
    if not models:
        return None

    attachment_context, _ = _build_attachment_context(attachments)
    llm_input = content
    if attachment_context:
        llm_input = f"{content}\n\n[用户上传附件上下文]\n{attachment_context}"

    prompt = build_prompt(
        agent["agent_id"],
        agent["domain"],
        llm_input,
        generate_symbolic_message(llm_input, "text", session_id),
        models[0].get("prompt", "") if models else "",
    )

    selected = models[0]
    adapter = adapter_manager.get_adapter(selected.get("provider", "mock"))

    async def stream():
        started = time.perf_counter()
        full = ""
        try:
            async for chunk in adapter.stream_prompt(prompt, selected.get("model_name", "mock"), decrypt_secret(selected.get("api_key", "")), selected.get("base_url", "")):
                if token and token.cancelled:
                    break
                full += chunk
                if chunk:
                    yield chunk
            _update_runtime(selected, True, (time.perf_counter() - started) * 1000)
        except Exception:
            _update_runtime(selected, False, (time.perf_counter() - started) * 1000)
            fallback = "模型调用失败，请稍后重试。"
            yield fallback
            full += fallback

        content_out = normalize_agent_output(agent["agent_id"], full, content)
        usage = adapter.last_usage
        if usage and usage.get("total_tokens", 0) > 0:
            pt, ct, tt = usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]
        else:
            pt, ct, tt = max(1, len(content) // 4), max(1, len(content_out) // 4), max(1, len(content) // 4) + max(1, len(content_out) // 4)
        save_message(session_id, agent["agent_id"], content_out, "text", 0.95, None, pt, ct, tt)

    return stream()


def build_prompt(agent_id: str, domain: str, content: str, symbolic: dict, role_prompt: str) -> str:
    base = role_prompt or (
        "你是 AgentHub 多智能体平台中的领域 Agent，必须输出清晰、可执行、可审计的结果。\n\n"
        "【代码输出格式规范】当回复中包含代码、终端命令、脚本、SQL 或配置文件时，必须严格遵守以下格式：\n"
        "1. 全部代码、终端命令统一使用 ```[语言] 代码块格式，必须精准填写语言名称：\n"
        "   - Python 代码 → python\n"
        "   - JavaScript/TypeScript 前端代码 → javascript / typescript\n"
        "   - Windows/Linux 终端命令 → bash\n"
        "   - 数据库语句 → sql\n"
        "   - 配置文件（JSON/YAML/TOML）→ json / yaml / toml\n"
        "2. 代码内容完整、语法无误，复制后可直接执行，不删减核心逻辑。\n"
        "3. 每一个代码块上方标注用途，多个代码片段依次编号（如：代码片段 1：创建配置文件）。\n"
        "4. 仅使用原生 Markdown，不插入 HTML、自定义标签。\n"
        "5. 纯文本说明和代码块分隔排版，结构清晰。"
    )
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


def _remove_repeated_text(text: str) -> str:
    if not text:
        return text
    lines = text.split('\n')
    unique_lines = []
    prev_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != prev_line:
            unique_lines.append(line)
            prev_line = stripped
    text = '\n'.join(unique_lines)

    cleaned = _remove_repeated_phrases(text)
    return cleaned


def _remove_repeated_phrases(text: str) -> str:
    """Detect and remove consecutively repeated phrases (12+ chars) within text."""
    n = len(text)
    if n < 24:
        return text
    # Scan with decreasing window sizes to catch both long and short repetitions
    for window in range(min(n // 2, 80), 11, -1):
        i = 0
        while i + window * 2 <= n:
            phrase = text[i:i + window]
            # Check if this phrase immediately repeats
            if text[i + window:i + window * 2] == phrase:
                # Remove the duplicate and restart scan from this position
                text = text[:i + window] + text[i + window * 2:]
                n = len(text)
                continue
            i += 1
    return text


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
        return _remove_repeated_text(model_output)
    if agent_id == "Review":
        return "Review 完成：结构符合 FastAPI + Next.js 分层方案，建议生产环境收紧 CORS、加入鉴权、限流和审计。"
    if agent_id == "Test":
        return "Test 完成：请验证 /api/health、/api/admin/model-config、/ws/session-1、DAG 状态机和 Git 接口。"
    if agent_id == "Deploy":
        return "Deploy 准备完成：前端 http://localhost:3000，后端 http://localhost:8000。高风险发布需管理员确认。"
    return f"已进入元调度：{original[:80]}。DAG 已生成，并按需激活领域 Agent。"
