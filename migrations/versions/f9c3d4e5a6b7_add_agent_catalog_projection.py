"""add agent catalog projection

Revision ID: f9c3d4e5a6b7
Revises: e8b2c3d4f5a6
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    AGENT_CATALOG_PROJECTION_DOWN_REVISION,
    AGENT_CATALOG_PROJECTION_DOWNGRADE,
    AGENT_CATALOG_PROJECTION_REVISION,
    AGENT_CATALOG_PROJECTION_UPGRADE,
)

revision: str = AGENT_CATALOG_PROJECTION_REVISION
down_revision: str | Sequence[str] | None = AGENT_CATALOG_PROJECTION_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in AGENT_CATALOG_PROJECTION_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in AGENT_CATALOG_PROJECTION_DOWNGRADE:
        op.execute(statement)
