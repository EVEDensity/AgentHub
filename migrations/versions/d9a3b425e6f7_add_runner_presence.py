"""Add authenticated Runner presence observations.

Revision ID: d9a3b425e6f7
Revises: c8f2a314d5e6
"""
from alembic import op

from app.db.migrations.runner_presence import (
    RUNNER_PRESENCE_DOWN_REVISION,
    RUNNER_PRESENCE_DOWNGRADE,
    RUNNER_PRESENCE_REVISION,
    RUNNER_PRESENCE_UPGRADE,
)

revision = RUNNER_PRESENCE_REVISION
down_revision = RUNNER_PRESENCE_DOWN_REVISION
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in RUNNER_PRESENCE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in RUNNER_PRESENCE_DOWNGRADE:
        op.execute(statement)
