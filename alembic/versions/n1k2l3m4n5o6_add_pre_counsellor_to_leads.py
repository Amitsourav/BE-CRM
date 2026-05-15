"""add pre_counsellor_id column on leads (FMC tile field)

Revision ID: n1k2l3m4n5o6
Revises: m0j1k2l3m4n5
Create Date: 2026-05-15

FMC has a two-step counsellor model: a Pre Counsellor warms up the
lead (telecaller) and a Counsellor closes it. The Counsellor maps
to assigned_agent_id; the Pre Counsellor needs its own column so
both can be shown side-by-side on the lead tile.

Admitverse leaves this NULL.
"""
from alembic import op
import sqlalchemy as sa


revision = "n1k2l3m4n5o6"
down_revision = "m0j1k2l3m4n5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "pre_counsellor_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "leads_pre_counsellor_id_fkey",
        "leads",
        "profiles",
        ["pre_counsellor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_leads_pre_counsellor_id",
        "leads",
        ["pre_counsellor_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_leads_pre_counsellor_id", table_name="leads")
    op.drop_constraint("leads_pre_counsellor_id_fkey", "leads", type_="foreignkey")
    op.drop_column("leads", "pre_counsellor_id")
