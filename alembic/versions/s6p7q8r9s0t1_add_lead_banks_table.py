"""add lead_banks table for per-lead multi-bank tracking

Revision ID: s6p7q8r9s0t1
Revises: r5o6p7q8r9s0
Create Date: 2026-05-16

FMC counsellors share a lead with multiple banks at different statuses
(Axis Sanctioned, Credila Applied, UniCred Under Review). Single
bank_name + bank_status on leads can't express that. Add a child table
and seed from existing data:
  - lead.bank_name + lead.bank_status → one row in lead_banks
  - lead.custom_fields.bank_history (dict of {bank: status_text}) → one
    row per entry, status mapped to the canonical enum where possible

Existing leads.bank_name + leads.bank_status are KEPT as the "primary"
bank for tile rendering. Auto-synced in service layer to the highest-
status entry (Disbursed > Sanctioned > PF Paid > Loan Login > Under
Review > Docs Reviewed > Applied).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "s6p7q8r9s0t1"
down_revision = "r5o6p7q8r9s0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_banks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bank_name", sa.String(100), nullable=False),
        sa.Column("bank_status", sa.dialects.postgresql.ENUM(
            "applied", "docs_reviewed", "under_review", "loan_login",
            "sanctioned", "pf_paid", "disbursed",
            name="bank_status", create_type=False,
        ), nullable=False, server_default=sa.text("'applied'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("lead_id", "bank_name", name="uniq_lead_banks_lead_bank"),
    )
    op.create_index("ix_lead_banks_lead_id", "lead_banks", ["lead_id"])
    op.create_index("ix_lead_banks_company_id", "lead_banks", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_banks_company_id", table_name="lead_banks")
    op.drop_index("ix_lead_banks_lead_id", table_name="lead_banks")
    op.drop_table("lead_banks")
