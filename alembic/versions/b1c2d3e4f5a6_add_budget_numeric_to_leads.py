"""add budget_amount + budget_currency numeric mirror on leads (Admitverse)

Revision ID: b1c2d3e4f5a6
Revises: a4b5c6d7e8f9
Create Date: 2026-06-17

Admitverse budgets come in multiple currencies (GBP/USD/EUR/INR). The
free-text `budget` column stays for display; these two columns hold the
parsed numeric value + its currency so the AV Kanban budget-range filter
can compare numbers within a currency. Analog of FMC's loan_amount_lakh,
but multi-currency. FMC leaves both NULL.
"""
from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("budget_amount", sa.Numeric(14, 2), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "budget_currency",
            sa.String(length=3),
            nullable=True,
            server_default="INR",
        ),
    )


def downgrade() -> None:
    op.drop_column("leads", "budget_currency")
    op.drop_column("leads", "budget_amount")
