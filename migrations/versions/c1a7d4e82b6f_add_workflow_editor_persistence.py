"""Add workflow editor contracts, versions, and drafts.

Revision ID: c1a7d4e82b6f
Revises: 9c24f8e621d1
Create Date: 2026-07-20
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c1a7d4e82b6f"
down_revision: Union[str, Sequence[str], None] = "9c24f8e621d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS edges_json TEXT NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE agent_routes ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 1")
    op.execute(
        """CREATE TABLE IF NOT EXISTS workflow_drafts (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            workflow_id INTEGER,
            draft_key TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            base_version INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, draft_key)
        )"""
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_drafts_user_updated "
        "ON workflow_drafts(user_id, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_drafts")
    op.execute("ALTER TABLE agent_routes DROP COLUMN IF EXISTS schema_version")
    op.execute("ALTER TABLE agent_routes DROP COLUMN IF EXISTS version")
    op.execute("ALTER TABLE agent_routes DROP COLUMN IF EXISTS edges_json")
