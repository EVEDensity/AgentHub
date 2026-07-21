"""Add cognitive memory metadata to execution history.

Revision ID: 9c24f8e621d1
Revises: ff209a40779d
Create Date: 2026-07-20
"""

from typing import Sequence, Union

from alembic import op


revision: str = "9c24f8e621d1"
down_revision: Union[str, Sequence[str], None] = "ff209a40779d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS "
        "memory_type TEXT NOT NULL DEFAULT 'episodic'"
    )
    op.execute(
        "ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS "
        "memory_scope TEXT NOT NULL DEFAULT 'session'"
    )
    op.execute(
        "ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS "
        "memory_source TEXT NOT NULL DEFAULT 'task_execution'"
    )
    op.execute(
        "ALTER TABLE task_execution_history ADD COLUMN IF NOT EXISTS "
        "memory_version INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_teh_memory_type_scope "
        "ON task_execution_history(memory_type, memory_scope, session_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_teh_memory_type_scope")
    op.execute("ALTER TABLE task_execution_history DROP COLUMN IF EXISTS memory_version")
    op.execute("ALTER TABLE task_execution_history DROP COLUMN IF EXISTS memory_source")
    op.execute("ALTER TABLE task_execution_history DROP COLUMN IF EXISTS memory_scope")
    op.execute("ALTER TABLE task_execution_history DROP COLUMN IF EXISTS memory_type")
