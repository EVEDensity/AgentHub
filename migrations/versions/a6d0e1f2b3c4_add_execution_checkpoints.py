"""add durable execution checkpoints

Revision ID: a6d0e1f2b3c4
Revises: f5c9d0e1a2b3
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    EXECUTION_CHECKPOINT_DOWN_REVISION,
    EXECUTION_CHECKPOINT_DOWNGRADE,
    EXECUTION_CHECKPOINT_REVISION,
    EXECUTION_CHECKPOINT_UPGRADE,
)

revision: str = EXECUTION_CHECKPOINT_REVISION
down_revision: str | Sequence[str] | None = EXECUTION_CHECKPOINT_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in EXECUTION_CHECKPOINT_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in EXECUTION_CHECKPOINT_DOWNGRADE:
        op.execute(statement)
