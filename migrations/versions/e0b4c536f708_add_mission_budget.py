"""Add Mission budget accounts, reservations and immutable receipts.

Revision ID: e0b4c536f708
Revises: d9a3b425e6f7
"""
from alembic import op

from app.db.migrations.mission_budget import (
    MISSION_BUDGET_DOWN_REVISION,
    MISSION_BUDGET_DOWNGRADE,
    MISSION_BUDGET_REVISION,
    MISSION_BUDGET_UPGRADE,
)

revision = MISSION_BUDGET_REVISION
down_revision = MISSION_BUDGET_DOWN_REVISION
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in MISSION_BUDGET_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in MISSION_BUDGET_DOWNGRADE:
        op.execute(statement)
