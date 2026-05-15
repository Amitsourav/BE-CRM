"""add dnp_count column on leads (FMC DNP attempt counter)

Revision ID: p3m4n5o6p7q8
Revises: o2l3m4n5o6p7
Create Date: 2026-05-15

FMC team wants to see how many times a lead has been DNP'd. The
original Google Sheet tracked DNP-1 through DNP-6 inline in the
pipeline status; this column re-introduces that as a dedicated
counter so the Kanban card can render "DNP-3" badges.

Auto-incremented in StageMachine whenever a lead transitions into
the 'dnp' stage. Admitverse uses dnp_pre_qualified / dnp_post_qualified
stages instead and ignores this counter.
"""
from alembic import op
import sqlalchemy as sa


revision = "p3m4n5o6p7q8"
down_revision = "o2l3m4n5o6p7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "dnp_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("leads", "dnp_count")
