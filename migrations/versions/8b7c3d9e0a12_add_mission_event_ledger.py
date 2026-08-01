"""add mission event ledger

Revision ID: 8b7c3d9e0a12
Revises: 4f6d2a8c901b
"""

from typing import Sequence, Union

from alembic import op

from app.db.migrations.mission_control_plane import (
    MISSION_EVENT_LEDGER_DOWN_REVISION,
    MISSION_EVENT_LEDGER_DOWNGRADE,
    MISSION_EVENT_LEDGER_REVISION,
    MISSION_EVENT_LEDGER_UPGRADE,
)

revision: str = MISSION_EVENT_LEDGER_REVISION
down_revision: Union[str, Sequence[str], None] = MISSION_EVENT_LEDGER_DOWN_REVISION
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for statement in MISSION_EVENT_LEDGER_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in MISSION_EVENT_LEDGER_DOWNGRADE:
        op.execute(statement)
