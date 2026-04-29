"""lead_stage_logs.created_at use clock_timestamp() not now()

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-04-29

now() returns the transaction-start time, so multi-step auto-stage
transitions written in one transaction (lead→called and called→connected)
got identical timestamps. That broke timeline ordering and made audit
queries non-deterministic. clock_timestamp() returns wall-clock time per
row insertion, giving each row a distinct microsecond.
"""
from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE lead_stage_logs "
        "ALTER COLUMN created_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE lead_stage_logs "
        "ALTER COLUMN created_at SET DEFAULT now()"
    )
