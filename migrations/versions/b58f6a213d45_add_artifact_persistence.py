"""add artifact metadata persistence

Revision ID: b58f6a213d45
Revises: a47e5f102c34
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    ARTIFACT_PERSISTENCE_DOWN_REVISION,
    ARTIFACT_PERSISTENCE_DOWNGRADE,
    ARTIFACT_PERSISTENCE_REVISION,
    ARTIFACT_PERSISTENCE_UPGRADE,
)

revision: str = ARTIFACT_PERSISTENCE_REVISION
down_revision: str | Sequence[str] | None = ARTIFACT_PERSISTENCE_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in ARTIFACT_PERSISTENCE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in ARTIFACT_PERSISTENCE_DOWNGRADE:
        op.execute(statement)
