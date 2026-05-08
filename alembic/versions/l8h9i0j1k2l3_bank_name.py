"""add bank_name column to leads

Revision ID: l8h9i0j1k2l3
Revises: k7g8h9i0j1k2
Create Date: 2026-05-08

The enhanced FMC tile shows bank_status (where the loan application
is — applied / sanctioned / etc.) but didn't track WHICH bank. Adds
a free-text `bank_name` so the card can render "SBI · Applied" or
"Axis · Sanctioned".

Free text, not enum — banks change vendors and the user shouldn't
have to ship a code change every time a new lender shows up.
"""
from alembic import op
import sqlalchemy as sa


revision = "l8h9i0j1k2l3"
down_revision = "k7g8h9i0j1k2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "leads",
        sa.Column("bank_name", sa.String(length=100), nullable=True),
    )


def downgrade():
    op.drop_column("leads", "bank_name")
