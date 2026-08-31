"""lead_banks.commission_rate — the rate a file was sanctioned under

Revision ID: n4i5j6k7l8m9
Revises: m3h4i5j6k7l8
Create Date: 2026-08-31

Gross theoretical revenue is the commission FMC would earn if every
sanctioned loan drew down in full — the lender's rate applied to the
SANCTIONED amount, as against actual revenue which applies it to what was
disbursed. The gap between the two is loans approved but never fully
drawn, which is a number the CRM has never been able to produce.

Computing it needs a rate per file, not just per lender. Reading
banks.commission_rate at query time would mean renegotiating a lender
silently restates what every earlier file was theoretically worth — the
same trap bank_disbursements.commission_rate already avoids by
snapshotting. It would also make the two figures incomparable: gross
theoretical would move to today's rate while actual revenue stayed on the
rate captured at disbursement, so the gap between them would absorb a rate
change that has nothing to do with drawdown.

Nullable, and NOT backfilled. Of the 79 files at sanctioned-or-later, only
31 carry a sanctioned amount at all and just 2 carry a sanction date, so
there is nothing to compute a historical rate against for most of them.
Filling those in is a manual job; a file with no rate is reported as
missing rather than counted as zero.

Note this is not `lead_banks.roi`, which is the interest rate the STUDENT
pays the bank.
"""
import sqlalchemy as sa
from alembic import op


revision = "n4i5j6k7l8m9"
down_revision = "m3h4i5j6k7l8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "lead_banks",
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=True),
    )


def downgrade():
    op.drop_column("lead_banks", "commission_rate")
