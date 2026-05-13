"""add budget column on leads (Admitverse tile field)

Revision ID: m0j1k2l3m4n5
Revises: l9i0j1k2l3m4
Create Date: 2026-05-13

Admitverse Kanban tile needs a counsellor-entered budget figure
(free text — same shape as FMC's loan_amount but a separate field
to keep brand semantics clean). FMC backend ignores it; FMC FE
never reads it. Adding to the shared model so both backends boot
against either DB without schema-mismatch crashes.
"""
from alembic import op
import sqlalchemy as sa


revision = "m0j1k2l3m4n5"
down_revision = "l9i0j1k2l3m4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("budget", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leads", "budget")
