"""Preserve legacy sessions and add explicit v1 workspace ownership.

Revision ID: c8f2a314d5e6
Revises: b7e1f203c4d5
"""
from __future__ import annotations

from alembic import op

from app.db.migrations.session_workspace import (
    SESSION_WORKSPACE_DOWN_REVISION,
    SESSION_WORKSPACE_DOWNGRADE,
    SESSION_WORKSPACE_REVISION,
    SESSION_WORKSPACE_UPGRADE,
)

revision = SESSION_WORKSPACE_REVISION
down_revision = SESSION_WORKSPACE_DOWN_REVISION
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in SESSION_WORKSPACE_UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    # Keep all additive data during application rollback (ADR-0113).
    for statement in SESSION_WORKSPACE_DOWNGRADE:
        op.execute(statement)
