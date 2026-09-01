"""bank_disbursements.disbursed_on becomes nullable

Revision ID: q7l8m9n0o1p2
Revises: p6k7l8m9n0o1
Create Date: 2026-09-01

Making the date mandatory was right for NEW disbursements and wrong for
the historical book, and the difference matters more than the rule.

FMC's revenue tracker holds 125 tranches. 38 of them carry a real
disbursed amount, a real commission and a real outstanding balance, but
no date — one is annotated "Date not recorded". Refusing those rows kept
₹4.02 cr of disbursement, ₹5.21 L of commission owed and ₹3.83 L still
outstanding out of the CRM entirely. That is 57% of the whole
outstanding book, discarded because a single column was blank.

A financial system must not lose money to a missing date. The amount is
the fact; the date is metadata about the fact. So the column is nullable
and a dateless row counts fully toward commission, GST, receipts and
outstanding — it is only excluded from AGEING, which genuinely cannot be
computed without it.

The capture gate is unchanged: `record_disbursement` still refuses a new
disbursement with no date, so nothing recorded from here on can be
dateless. This only lets history in.
"""
from alembic import op
import sqlalchemy as sa


revision = "q7l8m9n0o1p2"
down_revision = "p6k7l8m9n0o1"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "bank_disbursements", "disbursed_on",
        existing_type=sa.Date(), nullable=True,
    )


def downgrade():
    # Would fail while any dateless historical row exists, which is
    # correct — they must be given dates before the constraint returns.
    op.alter_column(
        "bank_disbursements", "disbursed_on",
        existing_type=sa.Date(), nullable=False,
    )
