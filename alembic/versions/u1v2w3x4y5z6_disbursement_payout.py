"""lead_banks: the payout owed to whoever supplied the lead

FMC does not keep all of its commission. When a lead comes from an
outside connector — Altera, MBA Aspire, arman, Nikhil Le Edu — a share
goes back to them. The tracker's month tabs carry it as "Payout
Sharing": June revenue Rs 7,50,432 less Rs 41,822 paid away is
Rs 7,08,609 actually kept. The CRM knew only what lenders owe FMC, so
every revenue figure it reported was GROSS.

On `lead_banks`, the FILE, and deliberately not on `bank_disbursements`.
The payout is agreed on the DEAL and computed off the sanction, while
commission is earned tranche by tranche off what is actually drawn. Paris
Joshi's Rs 8,832 is 0.4% of his whole Rs 27.6 L deal and cannot be
attributed to either of his two tranches. Aftar's Rs 4,870 is owed
against Rs 15.22 L sanctioned while only Rs 4.55 L has been drawn, so it
EXCEEDS the Rs 3,185 of commission earned so far — and Rs 3,653 of it has
already been paid. At tranche level neither row can be recorded at all.

Two amounts, mirroring the lender side's due/received. Amit confirmed
2026-09-06 that the sheet's two rival June columns are owed and paid, not
two attempts at one number, which is exactly why they disagreed only on
the part-paid rows.

`payout_to` is free text: these are ad-hoc individuals, not a managed
list, and forcing them into `lead_sources` would invent a taxonomy nobody
maintains.

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
"""
from alembic import op
import sqlalchemy as sa

revision = "u1v2w3x4y5z6"
down_revision = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_banks", sa.Column("payout_to", sa.String(100), nullable=True))
    op.add_column("lead_banks", sa.Column("payout_due", sa.Numeric(14, 2), nullable=True))
    op.add_column("lead_banks", sa.Column("payout_paid", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_banks", "payout_paid")
    op.drop_column("lead_banks", "payout_due")
    op.drop_column("lead_banks", "payout_to")
