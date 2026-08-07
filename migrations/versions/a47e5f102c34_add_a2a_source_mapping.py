"""add durable A2A source mapping

Revision ID: a47e5f102c34
Revises: 9c8d4e0f1b23
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    A2A_SOURCE_MAPPING_DOWN_REVISION,
    A2A_SOURCE_MAPPING_DOWNGRADE,
    A2A_SOURCE_MAPPING_REVISION,
    A2A_SOURCE_MAPPING_UPGRADE,
)

revision: str = A2A_SOURCE_MAPPING_REVISION
down_revision: str | Sequence[str] | None = A2A_SOURCE_MAPPING_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in A2A_SOURCE_MAPPING_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in A2A_SOURCE_MAPPING_DOWNGRADE:
        op.execute(statement)
