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
        created_at TEXT NOT NULL,
        owner_id TEXT NOT NULL DEFAULT '',
        visibility TEXT NOT NULL DEFAULT 'private'
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
        created_at TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS agent_registry (
        agent_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'sleeping',
        adapter_type TEXT NOT NULL DEFAULT 'mock',
        base_model_name TEXT NOT NULL DEFAULT '',
        config TEXT NOT NULL DEFAULT '{}',
        risk_level TEXT NOT NULL DEFAULT 'L1',
        duty_note TEXT NOT NULL DEFAULT '',
        display_name TEXT NOT NULL DEFAULT '',
        avatar_url TEXT NOT NULL DEFAULT '',
        capability_tags TEXT NOT NULL DEFAULT '[]',
        base_url TEXT NOT NULL DEFAULT '',
        api_key TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (agent_id, user_id)
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
    """CREATE TABLE IF NOT EXISTS task_execution_history (
        id SERIAL PRIMARY KEY,
        task_type TEXT NOT NULL,
        assigned_agent TEXT NOT NULL,
        success BOOLEAN NOT NULL,
        duration_ms INTEGER,
        tool_calls_count INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        error_type TEXT,
        session_id TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS idx_teh_agent_type ON task_execution_history(assigned_agent, task_type)""",
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
        name TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        trigger_keywords TEXT NOT NULL DEFAULT '[]',
        nodes_json TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_routes_name_user ON agent_routes(name, user_id)""",
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
    """CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        file_path TEXT NOT NULL,
        content TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    # ── Multi-user collaboration tables ───────────────────────────
    """CREATE TABLE IF NOT EXISTS session_members (
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'member',
        invited_by TEXT NOT NULL DEFAULT '',
        joined_at TEXT NOT NULL,
        PRIMARY KEY (session_id, user_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_sm_user ON session_members(user_id)""",
    """CREATE INDEX IF NOT EXISTS idx_sm_session ON session_members(session_id)""",
    """CREATE TABLE IF NOT EXISTS user_presence (
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'online',
        last_heartbeat TEXT NOT NULL,
        PRIMARY KEY (user_id, session_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_settings (
        user_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    )""",
    # ── MCP alert infrastructure ────────────────────────────────────
    """CREATE TABLE IF NOT EXISTS alert_rules (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        rule_type TEXT NOT NULL,
        condition_json TEXT NOT NULL DEFAULT '{}',
        severity TEXT NOT NULL DEFAULT 'warning',
        enabled INTEGER NOT NULL DEFAULT 1,
        notify_channels TEXT NOT NULL DEFAULT '["websocket"]',
        silence_window_seconds INTEGER NOT NULL DEFAULT 3600,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alert_history (
        id TEXT PRIMARY KEY,
        rule_id INTEGER,
        rule_name TEXT NOT NULL,
        severity TEXT NOT NULL,
        message TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}',
        acknowledged INTEGER NOT NULL DEFAULT 0,
        acknowledged_by TEXT NOT NULL DEFAULT '',
        triggered_at TEXT NOT NULL,
        resolved_at TEXT NOT NULL DEFAULT ''
    )""",
    # ── Performance indexes (critical query paths) ────────────────────
    # These are created via CREATE INDEX IF NOT EXISTS so they are
    # idempotent — safe to run on every startup.
    #
    # messages(session_id, created_at) — every chat load / scroll-back
    #    query filters on session_id and orders by created_at.
    """CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at DESC)""",
    # messages(user_id, created_at) — per-user message history queries.
    """CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages(user_id, created_at DESC)""",
    # agent_registry(user_id) — MCP dashboard and agent listing filter
    #    by user_id.  The composite PK (agent_id, user_id) doesn't help
    #    queries that scan by user_id alone.
    """CREATE INDEX IF NOT EXISTS idx_agent_registry_user ON agent_registry(user_id)""",
    # agent_registry(status) — dashboard health rollup (count by status).
    """CREATE INDEX IF NOT EXISTS idx_agent_registry_status ON agent_registry(status)""",
    # tool_call_log(session_id, created_at) — tool analytics & audit.
    """CREATE INDEX IF NOT EXISTS idx_tool_call_log_session_created ON tool_call_log(session_id, created_at DESC)""",
    # tool_call_log(agent_id, created_at) — per-agent tool usage stats.
    """CREATE INDEX IF NOT EXISTS idx_tool_call_log_agent_created ON tool_call_log(agent_id, created_at DESC)""",
    # audit_log(timestamp) — recent events stream on MCP dashboard.
    """CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log(timestamp DESC)""",
]


# ═══════════════════════════════════════════════════════════════════════
# Init entry point
# ═══════════════════════════════════════════════════════════════════════


async def ainit_db() -> None:
    """Create all tables and seed data on PostgreSQL (idempotent).

    Order: (1) Alembic migrations, (2) legacy DDL (idempotent fallback),
    (3) seed data.
    """
    await _ainit_postgresql()


# ═══════════════════════════════════════════════════════════════════════
# PostgreSQL implementation
# ═══════════════════════════════════════════════════════════════════════


async def _ainit_postgresql() -> None:
    """Create all tables and seed data on PostgreSQL."""
    import asyncio
    from app.db.session import aget_pool

    pool = await aget_pool()
    if pool is None:
        raise RuntimeError("PostgreSQL pool not available — check DATABASE_URL")

    async with pool.acquire() as conn:
        # ── Step 1: Apply Alembic migrations ────────────────────────
        await _apply_alembic_migrations(conn)

        # ── Step 2: Legacy DDL (idempotent fallback for non-Alembic tables) ──
        for ddl in _PG_DDL:
            try:
                await conn.execute(ddl)
            except Exception as exc:
                logger.warning("init_db PG DDL failed: %s — %s", exc, ddl[:80])

        logger.info("init_db: PostgreSQL tables created (%d DDL statements)", len(_PG_DDL))

        # ── Step 3: Runtime migrations for existing databases ───────
        await _migrate_agent_registry_pg(conn)
        await _migrate_multi_user_pg(conn)
        await _migrate_agent_routes_pg(conn)

        # ── Step 4: Seed default data ───────────────────────────────
        await _seed_users_pg(conn)
        await _seed_session_pg(conn)
        await _seed_agents_pg(conn)
        await _seed_templates_pg(conn)
        await _seed_agent_routes_pg(conn)
        await _seed_model_configs_pg(conn)

        logger.info("init_db: PostgreSQL seed data inserted")


async def _apply_alembic_migrations(conn) -> None:
    """Apply pending Alembic migrations via the asyncpg connection.

    Uses a simple version-check approach: queries ``alembic_version`` to
    find the current revision, then applies any migrations whose
    ``down_revision`` matches the current head.

    This avoids pulling in SQLAlchemy / psycopg2 for the async startup path
    while still giving us Alembic's versioned migration framework.
    """
    # Ensure the version table exists
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS alembic_version (
            version_num TEXT PRIMARY KEY
        )"""
    )

    # Check current version
    row = await conn.fetchrow("SELECT version_num FROM alembic_version LIMIT 1")
    current = row["version_num"] if row else None

    # Current head revision (must match migrations/versions/)
    head = "ff209a40779d"

    if current == head:
        logger.info("init_db: Alembic already at head (%s)", head)
        return

    if current is None:
        logger.info("init_db: Alembic fresh install — stamping head (%s)", head)
        # New database: stamp the head revision directly (tables will be
        # created by the legacy DDL path above).
        await conn.execute(
            "INSERT INTO alembic_version(version_num) VALUES($1) ON CONFLICT DO NOTHING",
            head,
        )
        return

    # Future: apply incremental migrations when current != head
    logger.warning(
        "init_db: Alembic version mismatch (current=%s, head=%s). "
        "Run 'alembic upgrade head' offline or contact the administrator.",
        current, head,
    )


async def _migrate_agent_registry_pg(conn) -> None:
    """Add columns that may not exist in older deployments (idempotent)."""
    migrations = [
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS display_name TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS avatar_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS capability_tags TEXT NOT NULL DEFAULT '[]'",
        # ── Avatar DB storage (v3.2): BYTEA + MIME type ────────────
        #   Moves avatar binary from filesystem to PostgreSQL so DB
        #   backups naturally cover avatar data.  NULL = no avatar.
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS avatar_data BYTEA",
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS avatar_mime TEXT NOT NULL DEFAULT ''",
        # ── Per-user agent separation: user_id + composite PK ─────
        "ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''",
    ]
    for m in migrations:
        try:
            await conn.execute(m)
        except Exception as exc:
            logger.warning("agent_registry migration skipped: %s", exc)

    # ── Convert single-column PK (agent_id) → composite PK (agent_id, user_id) ──
    # This is idempotent: if the composite PK already exists the DO block is a no-op.
    try:
        await conn.execute(
            """DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'agent_registry_pkey' AND contype = 'p'
              ) THEN
                -- Only proceed if user_id is NOT yet part of the PK
                IF NOT EXISTS (
                  SELECT 1 FROM information_schema.key_column_usage
                  WHERE constraint_name = 'agent_registry_pkey' AND column_name = 'user_id'
                ) THEN
                  ALTER TABLE agent_registry DROP CONSTRAINT agent_registry_pkey;
                  ALTER TABLE agent_registry ADD PRIMARY KEY (agent_id, user_id);
                END IF;
              END IF;
            END $$;"""
        )
    except Exception as exc:
        logger.warning("agent_registry PK migration skipped: %s", exc)


async def _migrate_multi_user_pg(conn) -> None:
    """Add multi-user collaboration columns and backfill existing data (idempotent)."""
    now_ts = now()

    # 1. Add new columns to existing tables
    col_migrations = [
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'private'",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''",
    ]
    for m in col_migrations:
        try:
            await conn.execute(m)
        except Exception as exc:
            logger.warning("multi_user migration skipped: %s", exc)

    # 2. Create session_members table if not exists
    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS session_members (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL DEFAULT 'member',
                invited_by TEXT NOT NULL DEFAULT '',
                joined_at TEXT NOT NULL,
                PRIMARY KEY (session_id, user_id)
            )"""
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sm_user ON session_members(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sm_session ON session_members(session_id)"
        )
    except Exception as exc:
        logger.warning("session_members migration skipped: %s", exc)

    # 3. Create user_presence table if not exists
    try:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS user_presence (
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'online',
                last_heartbeat TEXT NOT NULL,
                PRIMARY KEY (user_id, session_id)
            )"""
        )
    except Exception as exc:
        logger.warning("user_presence migration skipped: %s", exc)

    # 4. Backfill owner_id for sessions that have no owner
    orphan_sessions = await conn.fetch(
        "SELECT id FROM sessions WHERE owner_id = '' OR owner_id IS NULL"
    )
    for row in orphan_sessions:
        sid = row["id"]
        # Try to find the first human message sender in this session
        msg = await conn.fetchrow(
            "SELECT sender FROM messages WHERE session_id=$1 "
            "AND sender NOT IN ('system', 'Orchestrator', 'Architect', "
            "'CodeGen', 'Review', 'Test', 'Deploy', 'PM') "
            "ORDER BY created_at ASC LIMIT 1",
            sid,
        )
        owner_id = ""
        if msg:
            user_row = await conn.fetchrow(
                "SELECT id FROM users WHERE name=$1", msg["sender"]
            )
            if user_row:
                owner_id = user_row["id"]

        # Fallback: assign to admin
        if not owner_id:
            admin_row = await conn.fetchrow(
                "SELECT id FROM users WHERE role='admin' LIMIT 1"
            )
            if admin_row:
                owner_id = admin_row["id"]
            else:
                owner_id = DEFAULT_USER_ID

        await conn.execute(
            "UPDATE sessions SET owner_id=$1 WHERE id=$2", owner_id, sid
        )

        # Add owner as a session_member
        await conn.execute(
            "INSERT INTO session_members(session_id,user_id,role,joined_at) "
            "VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
            sid, owner_id, "owner", now_ts,
        )

    # 5. Make the default session public (so new users can see it)
    await conn.execute(
        "UPDATE sessions SET visibility='public' WHERE id=$1 AND visibility='private'",
        DEFAULT_SESSION_ID,
    )

    logger.info(
        "multi_user migration: %d sessions backfilled with owners",
        len(orphan_sessions),
    )


async def _migrate_agent_routes_pg(conn) -> None:
    """Add user_id column to agent_routes for per-user workflow isolation (idempotent)."""
    # 1. Add user_id column if not exists
    try:
        await conn.execute(
            "ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''"
        )
    except Exception as exc:
        logger.warning("agent_routes user_id migration skipped: %s", exc)

    # 2. Drop old unique constraint on name (single-column)
    try:
        await conn.execute("ALTER TABLE agent_routes DROP CONSTRAINT IF EXISTS agent_routes_name_key")
    except Exception as exc:
        logger.warning("agent_routes drop name_key skipped: %s", exc)

    # 3. Create composite unique index (name, user_id) if not exists
    try:
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_routes_name_user ON agent_routes(name, user_id)"
        )
    except Exception as exc:
        logger.warning("agent_routes composite unique index skipped: %s", exc)

    # 4. Backfill existing routes without user_id with the default admin user
    try:
        await conn.execute(
            "UPDATE agent_routes SET user_id=$1 WHERE user_id='' OR user_id IS NULL",
            DEFAULT_USER_ID,
        )
    except Exception as exc:
        logger.warning("agent_routes backfill user_id skipped: %s", exc)


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
