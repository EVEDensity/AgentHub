"""Historical bootstrap defaults shared by both database backends."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from app.config import DEFAULT_SESSION_ID, DEFAULT_USER_ID

logger = logging.getLogger("agenthub.db.init")

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_password_hash() -> str:
    salt = "agenthub-default-admin"
    digest = hashlib.pbkdf2_hmac("sha256", b"admin123", salt.encode("utf-8"), 120_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


async def _seed_users_pg(conn) -> None:
    await conn.execute(
        "INSERT INTO users(id,name,role,password_hash,created_at) "
        "VALUES($1,$2,$3,$4,$5) "
        "ON CONFLICT(id) DO UPDATE SET name=$2,role=$3,password_hash=$4",
        DEFAULT_USER_ID, "admin", "admin", _default_password_hash(), now(),
    )


async def _seed_session_pg(conn) -> None:
    await conn.execute(
        "INSERT INTO sessions(id,name,type,participants,active,created_at) "
        "VALUES($1,$2,$3,$4,$5,$6) "
        "ON CONFLICT(id) DO NOTHING",
        DEFAULT_SESSION_ID, "默认会话", "group",
        json.dumps([DEFAULT_USER_ID], ensure_ascii=False), 1, now(),
    )


async def _seed_agents_pg(conn) -> None:
    """Seed the 6 foundational multi-agent collaboration roles.

    Each agent has a clear input/output contract and a hard constraint
    that prevents it from overstepping its role.
    """
    agents = [
        (
            "Orchestrator", "orchestrator", "L2",
            "元调度器：接收用户意图，拆解任务并分派给领域 Agent，汇总结果。"
            "输入：用户原始需求 | 输出：任务分派方案、Agent 协同调度 | 约束：不替代领域 Agent 产出",
            "编排调度器",
            ["任务拆解", "Agent调度", "结果汇总"],
        ),
        (
            "Architect", "architect", "L1",
            "架构师：分析用户意图与项目结构，输出技术方案与文件影响范围。"
            "输入：用户意图、项目结构摘要 | 输出：技术方案、文件影响范围 | 约束：不直接写代码",
            "架构设计师",
            ["架构设计", "技术选型", "方案输出"],
        ),
        (
            "CodeGen", "codegen", "L2",
            "代码生成器：根据架构方案和上下文索引生成代码文件与 Diff 草案。"
            "输入：架构方案、上下文索引 | 输出：代码文件、Diff 草案 | 约束：不直接提交 Git",
            "代码生成器",
            ["代码生成", "文件创建", "多语言支持"],
        ),
        (
            "Review", "review", "L1",
            "代码审查员：审查 Diff 变更，对照规范与风险策略输出审查意见。"
            "输入：Diff、规范、风险策略 | 输出：审查意见、风险等级 | 约束：不修改部署配置",
            "代码审查员",
            ["代码审查", "安全审计", "规范检查"],
        ),
        (
            "Test", "test", "L1",
            "测试工程师：根据代码变更和测试策略生成测试用例与验证结果。"
            "输入：代码变更、测试策略 | 输出：测试结果、失败原因 | 约束：不绕过 Review 直接修改代码",
            "测试工程师",
            ["测试用例", "验证策略", "边界测试"],
        ),
        (
            "Implement", "implement", "L2",
            "实施工程师：将 CodeGen 生成的 Diff 落盘到工作区，处理合并冲突并跟踪落盘结果。"
            "输入：已审查 Diff | 输出：落盘文件清单、冲突报告 | 约束：不修改未审查代码",
            "实施工程师",
            ["文件落盘", "冲突解决", "变更跟踪"],
        ),
        (
            "Deploy", "deploy", "L3",
            "部署工程师：在 Review 通过后执行部署，生成预览 URL 和部署状态报告。"
            "输入：已确认 Diff、部署目标 | 输出：预览 URL、部署状态 | 约束：不部署未审查代码",
            "部署工程师",
            ["部署发布", "环境配置", "上线管理"],
        ),
    ]
    for agent_id, domain, risk, duty_note, display_name, capability_tags in agents:
        await conn.execute(
            "INSERT INTO agent_registry(agent_id,user_id,domain,status,adapter_type,risk_level,duty_note,display_name,capability_tags) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) "
            "ON CONFLICT(agent_id, user_id) DO UPDATE SET adapter_type=EXCLUDED.adapter_type, display_name=EXCLUDED.display_name, capability_tags=EXCLUDED.capability_tags",
            agent_id, "", domain, "sleeping", "", risk, duty_note, display_name, json.dumps(capability_tags, ensure_ascii=False),
        )


async def _seed_templates_pg(conn) -> None:
    templates = [
        (
            "前后端功能开发", "development",
            ["开发", "实现", "页面", "CRUD", "React", "FastAPI", "代码"],
            [
                {"id": "1", "domain": "architect", "agent": "Architect", "description": "分析需求并生成实现方案", "dependencies": [], "status": "PENDING"},
                {"id": "2", "domain": "codegen", "agent": "CodeGen", "description": "生成或修改前后端代码", "dependencies": ["1"], "status": "PENDING"},
                {"id": "3", "domain": "review", "agent": "Review", "description": "审查代码与风险点", "dependencies": ["2"], "status": "PENDING"},
                {"id": "4", "domain": "test", "agent": "Test", "description": "给出测试建议与验证结果", "dependencies": ["2"], "status": "PENDING"},
            ],
        ),
        (
            "部署发布流程", "deployment",
            ["部署", "deploy", "发布", "预览", "上线"],
            [
                {"id": "1", "domain": "review", "agent": "Review", "description": "检查发布风险", "dependencies": [], "status": "PENDING"},
                {"id": "2", "domain": "test", "agent": "Test", "description": "执行发布前验证", "dependencies": ["1"], "status": "PENDING"},
                {"id": "3", "domain": "deploy", "agent": "Deploy", "description": "执行部署并生成预览地址", "dependencies": ["1", "2"], "status": "PENDING"},
            ],
        ),
    ]
    for name, category, keywords, nodes in templates:
        existing = await conn.fetchval(
            "SELECT id FROM dag_templates WHERE name=$1", name,
        )
        if existing:
            continue
        dag_json = {"total": len(nodes), "completed": 0, "nodes": nodes}
        await conn.execute(
            "INSERT INTO dag_templates(name,category,keywords,dag_json,created_at) "
            "VALUES($1,$2,$3,$4,$5)",
            name, category, json.dumps(keywords, ensure_ascii=False),
            json.dumps(dag_json, ensure_ascii=False), now(),
        )


async def _seed_agent_routes_pg(conn) -> None:
    routes = [
        (
            "标准研发闭环",
            "Architect → CodeGen → Review/Test 的默认开发路线，适合常规功能开发。",
            ["开发", "实现", "代码", "页面", "接口", "FastAPI", "React"],
            1,
            [
                {"id": "architect", "domain": "architect", "agent": "Architect", "description": "分析需求并确定实现边界", "dependencies": [], "status": "PENDING"},
                {"id": "codegen", "domain": "codegen", "agent": "CodeGen", "description": "生成或修改代码", "dependencies": ["architect"], "status": "PENDING"},
                {"id": "review", "domain": "review", "agent": "Review", "description": "审查代码质量和风险", "dependencies": ["codegen"], "status": "PENDING"},
                {"id": "test", "domain": "test", "agent": "Test", "description": "生成验证建议和测试清单", "dependencies": ["codegen"], "status": "PENDING"},
            ],
        ),
        (
            "快速代码生成",
            "CodeGen → Review 的轻量路线，适合小文件、小接口、局部修改。",
            ["快速", "小改", "生成", "路由", "组件"],
            0,
            [
                {"id": "codegen", "domain": "codegen", "agent": "CodeGen", "description": "快速生成代码", "dependencies": [], "status": "PENDING"},
                {"id": "review", "domain": "review", "agent": "Review", "description": "轻量审查", "dependencies": ["codegen"], "status": "PENDING"},
            ],
        ),
        (
            "发布部署闭环",
            "Review → Test → Deploy 的发布路线，适合预览、部署、上线流程。",
            ["部署", "发布", "上线", "预览", "deploy"],
            0,
            [
                {"id": "review", "domain": "review", "agent": "Review", "description": "检查发布风险", "dependencies": [], "status": "PENDING"},
                {"id": "test", "domain": "test", "agent": "Test", "description": "执行发布前验证", "dependencies": ["review"], "status": "PENDING"},
                {"id": "deploy", "domain": "deploy", "agent": "Deploy", "description": "执行部署并生成预览地址", "dependencies": ["review", "test"], "status": "PENDING"},
            ],
        ),
    ]
    for name, description, keywords, is_default, nodes in routes:
        existing = await conn.fetchval(
            "SELECT id FROM agent_routes WHERE name=$1 AND user_id=$2", name, DEFAULT_USER_ID,
        )
        if existing:
            continue
        await conn.execute(
            "INSERT INTO agent_routes(name,user_id,description,trigger_keywords,nodes_json,"
            "is_default,active,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            name, DEFAULT_USER_ID, description, json.dumps(keywords, ensure_ascii=False),
            json.dumps(nodes, ensure_ascii=False), is_default, 1, now(), now(),
        )


async def _seed_model_configs_pg(conn) -> None:
    """Seed 6 default LLM provider entries so the admin model-config panel is non-empty.

    All seeded entries have empty API keys — the admin fills them in via the UI.
    Each entry is inserted only if no row with the same (provider, model_name) exists.
    """
    defaults = [
        ("openai", "GPT-4o", "https://api.openai.com/v1"),
        ("anthropic", "Claude Opus 4.8", "https://api.anthropic.com"),
        ("deepseek", "DeepSeek-V3", "https://api.deepseek.com"),
        ("zhipu", "GLM-4", "https://open.bigmodel.cn/api/paas/v4"),
        ("qwen", "Qwen-Max", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("doubao", "Doubao-Pro", "https://ark.cn-beijing.volces.com/api/v3"),
    ]
    for provider, model_name, base_url in defaults:
        exists = await conn.fetchval(
            "SELECT id FROM model_configs WHERE provider=$1 AND model_name=$2",
            provider, model_name,
        )
        if exists:
            continue
        await conn.execute(
            "INSERT INTO model_configs(provider, model_name, api_key, api_key_hash, base_url, is_active, created_at) "
            "VALUES($1, $2, $3, $4, $5, $6, $7)",
            provider, model_name, "", "", base_url, 1, now(),
        )
    logger.info("init_db: seeded %d model configs", len(defaults))
