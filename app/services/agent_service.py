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
    # Check agent_registry for every @mentioned name (supports both Chinese
    # and English agent names, not just the hardcoded AGENTS set).
    for name in extract_mentions(content):
        agent = one_row(
            "SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=?",
            (name,),
        )
        if agent:
            return agent
    # No valid mention — fall back to user-configured default, then Orchestrator
    default_row = one_row("SELECT value FROM system_config WHERE key='default_chat_agent'")
    default_agent_id = default_row["value"] if default_row else "Orchestrator"
    agent = one_row("SELECT agent_id,domain,status,adapter_type,risk_level FROM agent_registry WHERE agent_id=?", (default_agent_id,))
    return agent or {"agent_id": "Orchestrator", "domain": "orchestrator", "adapter_type": "mock", "risk_level": "L2"}


def candidate_models_for_role(role: str) -> list[dict]:
    # 1) Explicit role bindings (role_bindings JOIN model_configs)
    rows = dict_rows(
        "SELECT mc.id,mc.provider,mc.model_name AS model_name,mc.api_key,mc.base_url,rb.prompt FROM role_bindings rb JOIN model_configs mc ON rb.model_config_id=mc.id WHERE rb.role=? AND mc.is_active=1 ORDER BY mc.id DESC",
        (role,),
    )
    if rows:
        return rows
    # 2) Agent's own config in agent_registry (adapter_type + base_model_name + base_url + api_key)
    agent_row = one_row("SELECT adapter_type,base_model_name,base_url,api_key FROM agent_registry WHERE agent_id=?", (role,))
    if agent_row and agent_row.get("adapter_type") and agent_row.get("adapter_type") != "mock":
        return [{
            "id": 0,
            "provider": agent_row["adapter_type"],
            "model_name": agent_row.get("base_model_name") or "ping",  # "ping" → adapter uses its default_model
            "api_key": agent_row.get("api_key") or "",
            "base_url": agent_row.get("base_url") or "",
            "prompt": "",
        }]
    # 3) Fallback: any active model_config
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
    ts = now()
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
                ts,
            ),
        )
        conn.execute("UPDATE sessions SET last_message_at=? WHERE id=?", (ts, session_id))


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
        prompt_tokens, completion_tokens, total_tokens = _estimate_token_usage(prompt, content_out)
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
        stream_failed = False
        emitted_any = False
        try:
            async for chunk in adapter.stream_prompt(prompt, selected.get("model_name", "mock"), decrypt_secret(selected.get("api_key", "")), selected.get("base_url", "")):
                if token and token.cancelled:
                    break
                full += chunk
                if chunk:
                    emitted_any = True
                    yield chunk
            _update_runtime(selected, True, (time.perf_counter() - started) * 1000)
        except Exception as exc:
            _update_runtime(selected, False, (time.perf_counter() - started) * 1000)
            stream_failed = True
            # Fall back to non-streaming (same path as the admin test button)
            try:
                result = await adapter.execute_prompt(prompt, selected.get("model_name", "mock"), decrypt_secret(selected.get("api_key", "")), selected.get("base_url", ""))
                if result:
                    full = result
                    if not emitted_any:
                        yield "<thinking>正在分析中...</thinking>\n\n"
                    yield result
                    _update_runtime(selected, True, (time.perf_counter() - started) * 1000)
                else:
                    raise RuntimeError("empty response")
            except Exception as fallback_exc:
                fallback = f"模型调用失败（流式: {exc}，非流式: {fallback_exc}）"
                if not emitted_any:
                    yield "<thinking>正在分析中...</thinking>\n\n"
                yield fallback
                full += fallback

        content_out = normalize_agent_output(agent["agent_id"], full, content)
        usage = adapter.last_usage
        if usage and usage.get("total_tokens", 0) > 0:
            pt, ct, tt = usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]
        else:
            pt, ct, tt = max(1, len(prompt) // 4), max(1, len(content_out) // 4), max(1, len(prompt) // 4) + max(1, len(content_out) // 4)
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
    return f"{base}\n\n# 核心交互规则（强制遵守，对外完全不可见）\n1. 禁止输出任何与你的决策过程、执行规则、平台规范相关的说明性文本，仅输出用户可见的自然对话内容。\n2. 流式输出时，直接逐段输出最终回复，不添加任何前置思考、分析、步骤说明。\n3. 回复需友好自然，同时清晰介绍你可提供的服务范围，引导用户提出具体需求。\n\n# 回复风格要求\n- 语言简洁、专业、友好，避免冗长；\n- 服务范围使用清晰的项目符号列出，便于阅读；\n- 结尾主动引导用户提供具体代码或项目信息。\n\nAgent: {agent_id}\nDomain: {domain}\n符号消息: {json.dumps(public_symbolic(symbolic), ensure_ascii=False)}\n用户需求: {content}"


def _estimate_token_usage(user_text: str, model_output: str) -> tuple[int, int, int]:
    prompt_tokens = max(1, len(user_text) // 4)
    completion_tokens = max(1, len(model_output) // 4)
    total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _remove_repeated_text(text: str) -> str:
    if not text:
        return text
    # 1. Remove consecutive duplicate lines
    lines = text.split('\n')
    unique_lines = []
    prev_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != prev_line:
            unique_lines.append(line)
            prev_line = stripped
        elif not stripped:
            unique_lines.append(line)
    text = '\n'.join(unique_lines)

    # 2. Remove adjacent repeated phrases (12-80 char windows)
    text = _remove_repeated_phrases(text)

    # 3. Remove non-adjacent repeated paragraphs (30+ chars, same content
    #    appearing again later in the output regardless of intervening text)
    paragraphs = re.split(r'\n\n+', text)
    seen: set[str] = set()
    unique_paras: list[str] = []
    for p in paragraphs:
        stripped = p.strip()
        if len(stripped) >= 30:
            if stripped in seen:
                continue  # duplicate paragraph — drop it
            seen.add(stripped)
        unique_paras.append(p)
    text = '\n\n'.join(unique_paras)

    return text


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
    return (
        "【多智能体身份卡片】\n\n"
        "一、模型基础信息\n"
        "- 模型定位：实习生协作代理（代码支持方向）\n"
        "- 输出风格：结构化、可执行、可审计\n"
        "- 典型应用：代码修改建议、缺陷定位、功能实现、重构与测试补充\n\n"
        "二、平台角色信息\n"
        "- 平台角色：AgentHub 多智能体执行单元\n"
        "- 岗位能力：需求理解、代码分析、变更建议、结果校验\n"
        "- 协作方式：按任务路由接入对应专业 Agent 联合处理\n\n"
        "三、交互引导\n"
        "请直接提交以下任一内容以开始执行：\n"
        "1) 需要分析或修改的代码片段/文件\n"
        "2) 当前遇到的报错现象与复现步骤\n"
        "3) 目标功能与验收标准\n"
        "我将基于你的输入给出分步方案与可落地结果。"
    )

