"""add decision expiry

Revision ID: c2f6a7b8d9e0
Revises: b1e5f6a7c8d9
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    DECISION_EXPIRY_DOWN_REVISION,
    DECISION_EXPIRY_DOWNGRADE,
    DECISION_EXPIRY_REVISION,
    DECISION_EXPIRY_UPGRADE,
)

revision: str = DECISION_EXPIRY_REVISION
down_revision: str | Sequence[str] | None = DECISION_EXPIRY_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in DECISION_EXPIRY_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DECISION_EXPIRY_DOWNGRADE:
        op.execute(statement)
