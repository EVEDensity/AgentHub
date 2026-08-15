"""add delegation persistence

Revision ID: d7a1b2c3e4f5
Revises: c69e7b324f56
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    DELEGATION_PERSISTENCE_DOWN_REVISION,
    DELEGATION_PERSISTENCE_DOWNGRADE,
    DELEGATION_PERSISTENCE_REVISION,
    DELEGATION_PERSISTENCE_UPGRADE,
)

revision: str = DELEGATION_PERSISTENCE_REVISION
down_revision: str | Sequence[str] | None = DELEGATION_PERSISTENCE_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in DELEGATION_PERSISTENCE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DELEGATION_PERSISTENCE_DOWNGRADE:
        op.execute(statement)
