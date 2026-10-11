"""Add content-minimized checkpoint resume metadata.

Revision ID: b7e1f203c4d5
Revises: a6d0e1f2b3c4
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.checkpoint_resume import (
    EXECUTION_CHECKPOINT_RESUME_DOWN_REVISION,
    EXECUTION_CHECKPOINT_RESUME_DOWNGRADE,
    EXECUTION_CHECKPOINT_RESUME_REVISION,
    EXECUTION_CHECKPOINT_RESUME_UPGRADE,
)

revision: str = EXECUTION_CHECKPOINT_RESUME_REVISION
down_revision: str | Sequence[str] | None = EXECUTION_CHECKPOINT_RESUME_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in EXECUTION_CHECKPOINT_RESUME_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in EXECUTION_CHECKPOINT_RESUME_DOWNGRADE:
        op.execute(statement)
