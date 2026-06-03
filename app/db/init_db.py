from __future__ import annotations

import json
import hashlib
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


# ═══════════════════════════════════════════════════════════════════════
# DDL — PostgreSQL
# ═══════════════════════════════════════════════════════════════════════

_PG_DDL = [
    # ── Core tables ────────────────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL,
        password_hash TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'group',
        participants TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 1,
        is_pinned INTEGER NOT NULL DEFAULT 0,
        last_message_at TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        sender TEXT NOT NULL,
        content TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'text',
        fidelity_score REAL DEFAULT 0.95,
        symbolic_json TEXT DEFAULT '{}',
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS agent_registry (
        agent_id TEXT PRIMARY KEY,
        domain TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'sleeping',
        adapter_type TEXT NOT NULL DEFAULT 'mock',
        base_model_name TEXT NOT NULL DEFAULT '',
        config TEXT NOT NULL DEFAULT '{}',
        risk_level TEXT NOT NULL DEFAULT 'L1',
        duty_note TEXT NOT NULL DEFAULT '',
        base_url TEXT NOT NULL DEFAULT '',
        api_key TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        dag_json TEXT NOT NULL,
        current_node_id TEXT,
        template_id INTEGER,
        agent_route_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS dag_templates (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        keywords TEXT NOT NULL,
        dag_json TEXT NOT NULL,
        usage_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS model_configs (
        id SERIAL PRIMARY KEY,
        provider TEXT NOT NULL,
        model_name TEXT NOT NULL,
        api_key TEXT NOT NULL DEFAULT '',
        api_key_hash TEXT NOT NULL DEFAULT '',
        base_url TEXT DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS role_bindings (
        role TEXT PRIMARY KEY,
        model_config_id INTEGER NOT NULL,
        prompt TEXT DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS agent_routes (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        trigger_keywords TEXT NOT NULL DEFAULT '[]',
        nodes_json TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        action TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        decision TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS system_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # ── Tool-calling infrastructure ────────────────────────────────
    """CREATE TABLE IF NOT EXISTS tool_definitions (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        parameters_json TEXT NOT NULL,
        return_type TEXT NOT NULL,
        examples_json TEXT NOT NULL DEFAULT '[]',
        risk_level TEXT NOT NULL DEFAULT 'L1',
        handler_type TEXT NOT NULL DEFAULT 'builtin',
        handler_config TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS agent_tool_bindings (
        agent_id TEXT NOT NULL,
        tool_id INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (agent_id, tool_id)
    )""",
    """CREATE TABLE IF NOT EXISTS tool_call_log (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        arguments_json TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}',
        success INTEGER NOT NULL DEFAULT 0,
        duration_ms REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tool_permission_rules (
        id SERIAL PRIMARY KEY,
        agent_id TEXT NOT NULL DEFAULT '*',
        tool_pattern TEXT NOT NULL,
        path_pattern TEXT NOT NULL DEFAULT '*',
        behavior TEXT NOT NULL DEFAULT 'ask',
        source TEXT NOT NULL DEFAULT 'user',
        priority INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tool_hook_configs (
        id SERIAL PRIMARY KEY,
        hook_name TEXT NOT NULL,
        tool_name TEXT,
        hook_type TEXT NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
]


# ═══════════════════════════════════════════════════════════════════════
# Init entry point
# ═══════════════════════════════════════════════════════════════════════


async def ainit_db() -> None:
    """Create all tables and seed data on PostgreSQL (idempotent)."""
    await _ainit_postgresql()


# ═══════════════════════════════════════════════════════════════════════
# PostgreSQL implementation
# ═══════════════════════════════════════════════════════════════════════


async def _ainit_postgresql() -> None:
    """Create all tables and seed data on PostgreSQL."""
    from app.db.session import aget_pool

    pool = await aget_pool()
    if pool is None:
        raise RuntimeError("PostgreSQL pool not available — check DATABASE_URL")

    async with pool.acquire() as conn:
        # ── Create tables ──────────────────────────────────────────
        for ddl in _PG_DDL:
            try:
                await conn.execute(ddl)
            except Exception as exc:
                logger.warning("init_db PG DDL failed: %s — %s", exc, ddl[:80])

        logger.info("init_db: PostgreSQL tables created (%d DDL statements)", len(_PG_DDL))

        # ── Seed default data (ON CONFLICT DO NOTHING = idempotent) ──
        await _seed_users_pg(conn)
        await _seed_session_pg(conn)
        await _seed_agents_pg(conn)
        await _seed_templates_pg(conn)
        await _seed_agent_routes_pg(conn)

        logger.info("init_db: PostgreSQL seed data inserted")


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
        ),
        (
            "Architect", "architect", "L1",
            "架构师：分析用户意图与项目结构，输出技术方案与文件影响范围。"
            "输入：用户意图、项目结构摘要 | 输出：技术方案、文件影响范围 | 约束：不直接写代码",
        ),
        (
            "CodeGen", "codegen", "L2",
            "代码生成器：根据架构方案和上下文索引生成代码文件与 Diff 草案。"
            "输入：架构方案、上下文索引 | 输出：代码文件、Diff 草案 | 约束：不直接提交 Git",
        ),
        (
            "Review", "review", "L1",
            "代码审查员：审查 Diff 变更，对照规范与风险策略输出审查意见。"
            "输入：Diff、规范、风险策略 | 输出：审查意见、风险等级 | 约束：不修改部署配置",
        ),
        (
            "Test", "test", "L1",
            "测试工程师：根据代码变更和测试策略生成测试用例与验证结果。"
            "输入：代码变更、测试策略 | 输出：测试结果、失败原因 | 约束：不绕过 Review 直接修改代码",
        ),
        (
            "Deploy", "deploy", "L3",
            "部署工程师：在 Review 通过后执行部署，生成预览 URL 和部署状态报告。"
            "输入：已确认 Diff、部署目标 | 输出：预览 URL、部署状态 | 约束：不部署未审查代码",
        ),
    ]
    for agent_id, domain, risk, duty_note in agents:
        await conn.execute(
            "INSERT INTO agent_registry(agent_id,domain,status,adapter_type,risk_level,duty_note) "
            "VALUES($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT(agent_id) DO NOTHING",
            agent_id, domain, "sleeping", "mock", risk, duty_note,
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
            "SELECT id FROM agent_routes WHERE name=$1", name,
        )
        if existing:
            continue
        await conn.execute(
            "INSERT INTO agent_routes(name,description,trigger_keywords,nodes_json,"
            "is_default,active,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
            name, description, json.dumps(keywords, ensure_ascii=False),
            json.dumps(nodes, ensure_ascii=False), is_default, 1, now(), now(),
        )
