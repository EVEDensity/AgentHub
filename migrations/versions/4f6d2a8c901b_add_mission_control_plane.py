"""add mission control plane

Revision ID: 4f6d2a8c901b
Revises: c1a7d4e82b6f
"""

from typing import Sequence, Union

from alembic import op

from app.db.migrations.mission_control_plane import (
    MISSION_CONTROL_PLANE_DOWNGRADE,
    MISSION_CONTROL_PLANE_DOWN_REVISION,
    MISSION_CONTROL_PLANE_REVISION,
    MISSION_CONTROL_PLANE_UPGRADE,
)

revision: str = MISSION_CONTROL_PLANE_REVISION
down_revision: Union[str, Sequence[str], None] = MISSION_CONTROL_PLANE_DOWN_REVISION
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for statement in MISSION_CONTROL_PLANE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in MISSION_CONTROL_PLANE_DOWNGRADE:
        op.execute(statement)
