"""Initial AgentHub schema.

Revision ID: ff209a40779d
Revises: None
Create Date: 2026-06-09

This migration captures the complete database schema as of AgentHub v3.2.
It is idempotent — all statements use IF NOT EXISTS / IF EXISTS so it can
be run against both fresh and existing databases.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'ff209a40779d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all core tables (idempotent)."""
    # ── Users & sessions ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
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
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS session_members (
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'member',
            invited_by TEXT NOT NULL DEFAULT '',
            joined_at TEXT NOT NULL,
            PRIMARY KEY (session_id, user_id)
        )
    """)

    # ── Messages ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS messages (
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
        )
    """)

    # ── Agent registry ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_registry (
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
            avatar_data BYTEA,
            avatar_mime TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (agent_id, user_id)
        )
    """)

    # ── Tasks ───────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            dag_json TEXT NOT NULL,
            current_node_id TEXT,
            template_id INTEGER,
            agent_route_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS task_execution_history (
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
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS dag_templates (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            keywords TEXT NOT NULL,
            dag_json TEXT NOT NULL,
            usage_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_routes (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            trigger_keywords TEXT NOT NULL DEFAULT '[]',
            nodes_json TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # ── Model config ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_configs (
            id SERIAL PRIMARY KEY,
            provider TEXT NOT NULL,
            model_name TEXT NOT NULL,
            api_key TEXT NOT NULL DEFAULT '',
            api_key_hash TEXT NOT NULL DEFAULT '',
            base_url TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS role_bindings (
            role TEXT PRIMARY KEY,
            model_config_id INTEGER NOT NULL,
            prompt TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
    """)

    # ── Audit & config ──────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            action TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            decision TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            timestamp TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # ── Tool infrastructure ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_definitions (
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
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_tool_bindings (
            agent_id TEXT NOT NULL,
            tool_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (agent_id, tool_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_call_log (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            success INTEGER NOT NULL DEFAULT 0,
            duration_ms REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_permission_rules (
            id SERIAL PRIMARY KEY,
            agent_id TEXT NOT NULL DEFAULT '*',
            tool_pattern TEXT NOT NULL,
            path_pattern TEXT NOT NULL DEFAULT '*',
            behavior TEXT NOT NULL DEFAULT 'ask',
            source TEXT NOT NULL DEFAULT 'user',
            priority INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_hook_configs (
            id SERIAL PRIMARY KEY,
            hook_name TEXT NOT NULL,
            tool_name TEXT,
            hook_type TEXT NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    # ── Artifacts ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            content TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    # ── User presence & settings ────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_presence (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'online',
            last_heartbeat TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)

    # ── Alert infrastructure (MCP) ──────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
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
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS alert_history (
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
        )
    """)


def downgrade() -> None:
    """Drop all tables (irreversible in production — use with caution)."""
    tables = [
        "alert_history", "alert_rules",
        "user_settings", "user_presence",
        "artifacts",
        "tool_hook_configs", "tool_permission_rules", "tool_call_log",
        "agent_tool_bindings", "tool_definitions",
        "system_config", "audit_log",
        "role_bindings", "model_configs",
        "agent_routes", "dag_templates", "task_execution_history", "tasks",
        "agent_registry",
        "messages",
        "session_members", "sessions", "users",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
