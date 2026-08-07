"""add work unit persistence

Revision ID: 9c8d4e0f1b23
Revises: 8b7c3d9e0a12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    WORK_UNIT_PERSISTENCE_DOWN_REVISION,
    WORK_UNIT_PERSISTENCE_DOWNGRADE,
    WORK_UNIT_PERSISTENCE_REVISION,
    WORK_UNIT_PERSISTENCE_UPGRADE,
)

revision: str = WORK_UNIT_PERSISTENCE_REVISION
down_revision: str | Sequence[str] | None = WORK_UNIT_PERSISTENCE_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in WORK_UNIT_PERSISTENCE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in WORK_UNIT_PERSISTENCE_DOWNGRADE:
        op.execute(statement)
