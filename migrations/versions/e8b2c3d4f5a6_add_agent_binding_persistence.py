"""add agent binding persistence

Revision ID: e8b2c3d4f5a6
Revises: d7a1b2c3e4f5
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db.migrations.mission_control_plane import (
    AGENT_BINDING_PERSISTENCE_DOWN_REVISION,
    AGENT_BINDING_PERSISTENCE_DOWNGRADE,
    AGENT_BINDING_PERSISTENCE_REVISION,
    AGENT_BINDING_PERSISTENCE_UPGRADE,
)

revision: str = AGENT_BINDING_PERSISTENCE_REVISION
down_revision: str | Sequence[str] | None = AGENT_BINDING_PERSISTENCE_DOWN_REVISION
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in AGENT_BINDING_PERSISTENCE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in AGENT_BINDING_PERSISTENCE_DOWNGRADE:
        op.execute(statement)
