"""leads.expected_closure_month — when we EXPECT a deal to close

The revenue tracker's Student_Master carries a "Closure Month" for 118
students and the CRM has nowhere to put it, so it has no forecast at all:
every figure it reports is history.

Amit's definition (2026-09-04), and the important part: it is a
FORWARD-LOOKING TARGET, set early, revisable, and a dropped student keeps
the month it was expected to close in. It is NOT a record of when
anything happened. Using it as one is a real trap — dating tranches from
it put Rajwardhan's SECOND release in March, before that release existed.

A Date pinned to the first of the month rather than a string, so
date_trunc and range filters work without parsing. The index serves the
month filter on the loan-intelligence dashboard.

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
"""
from alembic import op
import sqlalchemy as sa

revision = "w3x4y5z6a7b8"
down_revision = "v2w3x4y5z6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("expected_closure_month", sa.Date(), nullable=True))
    op.create_index(
        "ix_leads_expected_closure_month",
        "leads", ["company_id", "expected_closure_month"],
        postgresql_where=sa.text("expected_closure_month IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_leads_expected_closure_month", table_name="leads")
    op.drop_column("leads", "expected_closure_month")
