"""materialize Contract lineage workspace ownership

Revision ID: f5c9d0e1a2b3
Revises: e4b8c9d0f1a2
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    CONTRACT_LINEAGE_OWNERSHIP_DOWN_REVISION,
    CONTRACT_LINEAGE_OWNERSHIP_DOWNGRADE,
    CONTRACT_LINEAGE_OWNERSHIP_REVISION,
    CONTRACT_LINEAGE_OWNERSHIP_UPGRADE,
)

revision: str = CONTRACT_LINEAGE_OWNERSHIP_REVISION
down_revision: str | Sequence[str] | None = CONTRACT_LINEAGE_OWNERSHIP_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in CONTRACT_LINEAGE_OWNERSHIP_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in CONTRACT_LINEAGE_OWNERSHIP_DOWNGRADE:
        op.execute(statement)
