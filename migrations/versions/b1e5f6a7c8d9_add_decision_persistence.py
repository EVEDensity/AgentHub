"""add decision persistence

Revision ID: b1e5f6a7c8d9
Revises: a0d4e5f6b7c8
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    DECISION_PERSISTENCE_DOWN_REVISION,
    DECISION_PERSISTENCE_DOWNGRADE,
    DECISION_PERSISTENCE_REVISION,
    DECISION_PERSISTENCE_UPGRADE,
)

revision: str = DECISION_PERSISTENCE_REVISION
down_revision: str | Sequence[str] | None = DECISION_PERSISTENCE_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in DECISION_PERSISTENCE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DECISION_PERSISTENCE_DOWNGRADE:
        op.execute(statement)
