"""bind each Mission to an exact immutable Contract revision

Revision ID: e4b8c9d0f1a2
Revises: d3a7b8c9e0f1
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    CONTRACT_REVISION_BINDING_DOWN_REVISION,
    CONTRACT_REVISION_BINDING_DOWNGRADE,
    CONTRACT_REVISION_BINDING_REVISION,
    CONTRACT_REVISION_BINDING_UPGRADE,
)

revision: str = CONTRACT_REVISION_BINDING_REVISION
down_revision: str | Sequence[str] | None = CONTRACT_REVISION_BINDING_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in CONTRACT_REVISION_BINDING_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in CONTRACT_REVISION_BINDING_DOWNGRADE:
        op.execute(statement)
