"""bank_disbursements + banks.commission_rate — the commission ledger

Revision ID: m3h4i5j6k7l8
Revises: l2g3h4i5j6k7
Create Date: 2026-08-26

FMC earns a percentage of what a lender disburses, and until now the CRM
recorded none of it. `lead_banks` knew a file reached `disbursed` but not
how much left the bank, not when, and there was no commission concept in
the schema at all. On the day this was written 78 leads sat at the
`disbursed` stage with 17 amounts and ZERO dates between them, while the
actual ledger — what was billed, what was paid, what is still owed —
lived on a spreadsheet outside the system where nobody reconciled it.

Two changes:

1. `banks.commission_rate` — what each lender pays, as a percentage.
   Nullable: it has to be entered per lender and nothing can guess it. A
   lender with no rate cannot have commission computed, and the report is
   expected to say so rather than quietly bill at zero.

2. `bank_disbursements` — one row per tranche released. This is the grain
   FMC earns on, and it is a child of `lead_banks` rather than a column
   on it because a loan can be released in parts over years, each part
   billable separately.

`tds_deducted` deserves a note: Indian lenders withhold TDS under s.194H
before paying commission. Without recording it, every receipt appears
2-5% short and the shortfall column becomes noise nobody trusts — which
would defeat the purpose of the whole table.

No backfill here. The 34 historical `disbursed` rows have no recoverable
disbursement date (`won_time` is NULL on all 78 leads, `sanction_date` is
set on 3 of 2,410 rows), and inventing dates would silently corrupt every
ageing and monthly figure built on top. Those rows get filled in
deliberately, by hand, from the spreadsheet.
"""
import sqlalchemy as sa
from alembic import op


revision = "m3h4i5j6k7l8"
down_revision = "l2g3h4i5j6k7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "banks",
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=True),
    )

    op.create_table(
        "bank_disbursements",
        sa.Column(
            "id", sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True, server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "lead_bank_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lead_banks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "lead_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("bank_name", sa.String(100), nullable=False),
        # Rupees, matching lead_banks.loan_amount. The API takes lakhs and
        # converts, so the two money columns can't disagree on unit.
        sa.Column("disbursed_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("disbursed_on", sa.Date(), nullable=False),
        sa.Column("tranche_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("utr_reference", sa.String(100), nullable=True),
        # Snapshot of the rate that applied to THIS disbursement.
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("commission_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("gst_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "invoice_id", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("amount_received", sa.Numeric(14, 2), nullable=True),
        sa.Column("tds_deducted", sa.Numeric(14, 2), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.Column("payment_reference", sa.String(100), nullable=True),
        sa.Column("write_off_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=True),
        sa.Column(
            "created_by", sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # Entering the same tranche twice is the obvious way for a
        # commission figure to silently double.
        sa.UniqueConstraint("lead_bank_id", "tranche_no", name="uniq_disbursement_tranche"),
        # Money must be positive; a zero-rupee disbursement is a data
        # entry mistake, not a real event.
        sa.CheckConstraint("disbursed_amount > 0", name="ck_disbursement_amount_positive"),
        sa.CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 100",
            name="ck_disbursement_rate_range",
        ),
    )

    op.create_index(
        "ix_bank_disbursements_company_date", "bank_disbursements",
        ["company_id", "disbursed_on"],
    )
    op.create_index(
        "ix_bank_disbursements_invoice", "bank_disbursements",
        ["company_id", "invoice_id"],
    )
    op.create_index(
        "ix_bank_disbursements_lead_bank", "bank_disbursements", ["lead_bank_id"],
    )
    # The query that finds money: disbursed, never billed.
    op.execute(
        "CREATE INDEX ix_bank_disbursements_unbilled "
        "ON bank_disbursements (company_id) WHERE invoice_id IS NULL"
    )


def downgrade():
    op.drop_index("ix_bank_disbursements_unbilled", table_name="bank_disbursements")
    op.drop_index("ix_bank_disbursements_lead_bank", table_name="bank_disbursements")
    op.drop_index("ix_bank_disbursements_invoice", table_name="bank_disbursements")
    op.drop_index("ix_bank_disbursements_company_date", table_name="bank_disbursements")
    op.drop_table("bank_disbursements")
    op.drop_column("banks", "commission_rate")
