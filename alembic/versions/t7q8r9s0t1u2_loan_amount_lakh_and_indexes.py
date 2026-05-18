"""loan_amount_lakh numeric column + pipeline filter indexes

Revision ID: t7q8r9s0t1u2
Revises: s6p7q8r9s0t1
Create Date: 2026-05-18

Adds a numeric mirror of the free-text `loan_amount` column so the
Kanban budget filter can compare numbers instead of guessing whether
"25 lakh" is bigger than "1cr". The text column stays untouched — the
counsellor still sees "25 lakh" in the UI exactly as they typed it.

Also pre-indexes the columns the new Kanban filter set hits hardest:
bank_name, target_country, target_intake, loan_amount_lakh, due_date.
Costs ~tens of KB each; keeps queries fast even at 50k+ leads.
"""
from alembic import op
import sqlalchemy as sa


revision = "t7q8r9s0t1u2"
down_revision = "s6p7q8r9s0t1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "loan_amount_lakh",
            sa.Numeric(precision=12, scale=2),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_leads_loan_amount_lakh",
        "leads",
        ["company_id", "loan_amount_lakh"],
    )
    op.create_index(
        "idx_leads_bank_name",
        "leads",
        ["company_id", "bank_name"],
    )
    # preferred_countries is text[] — GIN index supports the array
    # containment operator (`preferred_countries @> ARRAY['USA']`) that
    # the destination-country Kanban filter will use.
    op.create_index(
        "idx_leads_preferred_countries",
        "leads",
        ["preferred_countries"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_leads_target_intake",
        "leads",
        ["company_id", "target_intake"],
    )
    op.create_index(
        "idx_leads_due_date",
        "leads",
        ["company_id", "due_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_leads_due_date", table_name="leads")
    op.drop_index("idx_leads_target_intake", table_name="leads")
    op.drop_index("idx_leads_preferred_countries", table_name="leads")
    op.drop_index("idx_leads_bank_name", table_name="leads")
    op.drop_index("idx_leads_loan_amount_lakh", table_name="leads")
    op.drop_column("leads", "loan_amount_lakh")
