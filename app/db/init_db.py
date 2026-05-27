from __future__ import annotations

import json
import hashlib
from datetime import datetime

from app.config import DEFAULT_SESSION_ID, DEFAULT_USER_ID
from app.db.session import get_connection


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_password_hash() -> str:
    salt = "agenthub-default-admin"
    digest = hashlib.pbkdf2_hmac("sha256", b"admin123", salt.encode("utf-8"), 120_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,role TEXT NOT NULL,password_hash TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,name TEXT NOT NULL,type TEXT NOT NULL DEFAULT 'group',participants TEXT NOT NULL DEFAULT '[]',active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,sender TEXT NOT NULL,content TEXT NOT NULL,type TEXT NOT NULL DEFAULT 'text',fidelity_score REAL DEFAULT 0.95,symbolic_json TEXT DEFAULT '{}',created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_registry(agent_id TEXT PRIMARY KEY,domain TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'sleeping',adapter_type TEXT NOT NULL DEFAULT 'mock',base_model_name TEXT NOT NULL DEFAULT '',config TEXT NOT NULL DEFAULT '{}',risk_level TEXT NOT NULL DEFAULT 'L1',duty_note TEXT NOT NULL DEFAULT '',base_url TEXT NOT NULL DEFAULT '',api_key TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',dag_json TEXT NOT NULL,current_node_id TEXT,template_id INTEGER,agent_route_id INTEGER,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dag_templates(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,category TEXT NOT NULL,keywords TEXT NOT NULL,dag_json TEXT NOT NULL,usage_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS model_configs(id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT NOT NULL,model_name TEXT NOT NULL,api_key TEXT NOT NULL DEFAULT '',api_key_hash TEXT NOT NULL DEFAULT '',base_url TEXT DEFAULT '',is_active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS role_bindings(role TEXT PRIMARY KEY,model_config_id INTEGER NOT NULL,prompt TEXT DEFAULT '',updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_routes(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,description TEXT NOT NULL DEFAULT '',trigger_keywords TEXT NOT NULL DEFAULT '[]',nodes_json TEXT NOT NULL,is_default INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_log(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,agent_id TEXT NOT NULL,action TEXT NOT NULL,risk_level TEXT NOT NULL,decision TEXT NOT NULL,content_hash TEXT NOT NULL,payload_json TEXT NOT NULL DEFAULT '{}',timestamp TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL);
            """
        )
        migrate_existing_schema(conn)
        conn.execute("INSERT OR IGNORE INTO users(id,name,role,password_hash,created_at) VALUES(?,?,?,?,?)", (DEFAULT_USER_ID, "admin", "admin", _default_password_hash(), now()))
        conn.execute("UPDATE users SET name='admin', role='admin', password_hash=? WHERE id=?", (_default_password_hash(), DEFAULT_USER_ID))
        conn.execute(
            "INSERT OR IGNORE INTO sessions(id,name,type,participants,active,created_at) VALUES(?,?,?,?,?,?)",
            (DEFAULT_SESSION_ID, "默认会话", "group", json.dumps([DEFAULT_USER_ID], ensure_ascii=False), 1, now()),
        )
        for agent_id, domain, risk in [
            ("Orchestrator", "orchestrator", "L2"),
            ("Architect", "architect", "L1"),
            ("CodeGen", "codegen", "L2"),
            ("Review", "review", "L1"),
            ("Test", "test", "L1"),
            ("Deploy", "deploy", "L3"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO agent_registry(agent_id,domain,status,adapter_type,risk_level) VALUES(?,?,?,?,?)",
                (agent_id, domain, "sleeping", "mock", risk),
            )
        seed_templates(conn)
        seed_agent_routes(conn)


def migrate_existing_schema(conn) -> None:
    migrations = {
        "agent_registry": [
            "ALTER TABLE agent_registry ADD COLUMN base_model_name TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE agent_registry ADD COLUMN duty_note TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE agent_registry ADD COLUMN base_url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE agent_registry ADD COLUMN api_key TEXT NOT NULL DEFAULT ''",
        ],
        "users": ["ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''"],
        "messages": [
            "ALTER TABLE messages ADD COLUMN symbolic_json TEXT DEFAULT '{}'",
            "ALTER TABLE messages ADD COLUMN prompt_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN completion_tokens INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE messages ADD COLUMN total_tokens INTEGER NOT NULL DEFAULT 0",
        ],
        "tasks": ["ALTER TABLE tasks ADD COLUMN current_node_id TEXT", "ALTER TABLE tasks ADD COLUMN template_id INTEGER", "ALTER TABLE tasks ADD COLUMN agent_route_id INTEGER"],
        "model_configs": ["ALTER TABLE model_configs ADD COLUMN api_key TEXT NOT NULL DEFAULT ''"],
        "audit_log": ["ALTER TABLE audit_log ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"],
        "sessions": [
            "ALTER TABLE sessions ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN last_message_at TEXT NOT NULL DEFAULT ''",
        ],
    }
    for table, statements in migrations.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for statement in statements:
            column = statement.split("ADD COLUMN", 1)[1].strip().split()[0]
            if column not in existing:
                conn.execute(statement)


def seed_templates(conn) -> None:
    templates = [
        (
            "前后端功能开发",
            "development",
            ["开发", "实现", "页面", "CRUD", "React", "FastAPI", "代码"],
            [
                {"id": "1", "domain": "architect", "agent": "Architect", "description": "分析需求并生成实现方案", "dependencies": [], "status": "PENDING"},
                {"id": "2", "domain": "codegen", "agent": "CodeGen", "description": "生成或修改前后端代码", "dependencies": ["1"], "status": "PENDING"},
                {"id": "3", "domain": "review", "agent": "Review", "description": "审查代码与风险点", "dependencies": ["2"], "status": "PENDING"},
                {"id": "4", "domain": "test", "agent": "Test", "description": "给出测试建议与验证结果", "dependencies": ["2"], "status": "PENDING"},
            ],
        ),
        (
            "部署发布流程",
            "deployment",
            ["部署", "deploy", "发布", "预览", "上线"],
            [
                {"id": "1", "domain": "review", "agent": "Review", "description": "检查发布风险", "dependencies": [], "status": "PENDING"},
                {"id": "2", "domain": "test", "agent": "Test", "description": "执行发布前验证", "dependencies": ["1"], "status": "PENDING"},
                {"id": "3", "domain": "deploy", "agent": "Deploy", "description": "执行部署并生成预览地址", "dependencies": ["1", "2"], "status": "PENDING"},
            ],
        ),
    ]
    for name, category, keywords, nodes in templates:
        if conn.execute("SELECT id FROM dag_templates WHERE name=?", (name,)).fetchone():
            continue
        dag_json = {"total": len(nodes), "completed": 0, "nodes": nodes}
        conn.execute(
            "INSERT INTO dag_templates(name,category,keywords,dag_json,created_at) VALUES(?,?,?,?,?)",
            (name, category, json.dumps(keywords, ensure_ascii=False), json.dumps(dag_json, ensure_ascii=False), now()),
        )


def seed_agent_routes(conn) -> None:
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
        if conn.execute("SELECT id FROM agent_routes WHERE name=?", (name,)).fetchone():
            continue
        conn.execute(
            "INSERT INTO agent_routes(name,description,trigger_keywords,nodes_json,is_default,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (name, description, json.dumps(keywords, ensure_ascii=False), json.dumps(nodes, ensure_ascii=False), is_default, 1, now(), now()),
        )
