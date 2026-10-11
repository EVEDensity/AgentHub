"""Operational Runner observations; Mission/WorkUnit state is unchanged."""
from __future__ import annotations

from app.db.migrations.session_workspace import SESSION_WORKSPACE_REVISION

RUNNER_PRESENCE_REVISION = "d9a3b425e6f7"
RUNNER_PRESENCE_DOWN_REVISION = SESSION_WORKSPACE_REVISION
RUNNER_PRESENCE_UPGRADE = (
    """CREATE TABLE IF NOT EXISTS runner_presence(
        workspace_id TEXT NOT NULL,
        runner_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        adapter_type TEXT NOT NULL,
        work_unit_kind TEXT NOT NULL,
        supported_capabilities JSONB NOT NULL DEFAULT '[]',
        last_seen_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(workspace_id,runner_id,agent_id,adapter_type,work_unit_kind)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_runner_presence_binding
        ON runner_presence(workspace_id,agent_id,adapter_type,work_unit_kind,expires_at)""",
)
RUNNER_PRESENCE_DOWNGRADE = (
    "DROP TABLE IF EXISTS runner_presence",
)


async def upgrade_runner_presence_sqlite(connection) -> None:
    async with connection.transaction():
        for statement in RUNNER_PRESENCE_UPGRADE:
            await connection.execute(statement.replace("JSONB", "TEXT").replace("TIMESTAMPTZ", "TEXT"))
