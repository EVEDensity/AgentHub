"""separate Mission Artifact metadata from legacy session artifacts

Revision ID: d3a7b8c9e0f1
Revises: c2f6a7b8d9e0
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION,
    ARTIFACT_TABLE_OWNERSHIP_DOWNGRADE,
    ARTIFACT_TABLE_OWNERSHIP_REVISION,
    ARTIFACT_TABLE_OWNERSHIP_UPGRADE,
)

revision: str = ARTIFACT_TABLE_OWNERSHIP_REVISION
down_revision: str | Sequence[str] | None = ARTIFACT_TABLE_OWNERSHIP_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in ARTIFACT_TABLE_OWNERSHIP_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in ARTIFACT_TABLE_OWNERSHIP_DOWNGRADE:
        op.execute(statement)
