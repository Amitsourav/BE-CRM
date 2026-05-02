"""merge heads — b2c3d4e5f6a7 + d5e6f7a8b9c0

Two parallel branches both descended from 5a044326b9cf:
  - b2c3d4e5f6a7  (add_last_contacted_at)
  - c3d4e5f6a7b8 → d5e6f7a8b9c0  (lead_stage_logs index + clock_timestamp)

Alembic refuses to `upgrade head` when there are multiple heads. This
migration is a no-op merge — its only purpose is to give the tree a
single head again.

Revision ID: e0a1b2c3d4e5
Revises: b2c3d4e5f6a7, d5e6f7a8b9c0
Create Date: 2026-05-02
"""
from alembic import op  # noqa: F401


revision = "e0a1b2c3d4e5"
down_revision = ("b2c3d4e5f6a7", "d5e6f7a8b9c0")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op — this is a merge marker.
    pass


def downgrade() -> None:
    # No-op
    pass
