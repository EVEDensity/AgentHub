"""add evidence projection

Revision ID: c69e7b324f56
Revises: b58f6a213d45
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    EVIDENCE_PROJECTION_DOWN_REVISION,
    EVIDENCE_PROJECTION_DOWNGRADE,
    EVIDENCE_PROJECTION_REVISION,
    EVIDENCE_PROJECTION_UPGRADE,
)

revision: str = EVIDENCE_PROJECTION_REVISION
down_revision: str | Sequence[str] | None = EVIDENCE_PROJECTION_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in EVIDENCE_PROJECTION_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in EVIDENCE_PROJECTION_DOWNGRADE:
        op.execute(statement)
