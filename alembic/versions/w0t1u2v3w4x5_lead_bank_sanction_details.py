"""Sanction details on lead_banks (FMC)

Revision ID: w0t1u2v3w4x5
Revises: v9s0t1u2v3w4
Create Date: 2026-05-19

Once a bank moves to `sanctioned` status, the FMC team needs to record
the actual loan terms — Application ID, sanction date, loan amount, ROI,
tenure, processing fee, tranche schedule, and PF payment status. These
fields stay NULL until the bank reaches sanctioned (the API gates write
access).
"""
from alembic import op
import sqlalchemy as sa


revision = "w0t1u2v3w4x5"
down_revision = "v9s0t1u2v3w4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PF status enum — only two values per FMC's flow. create_type=False
    # is consistent with other enums in this codebase.
    pf_status_enum = sa.Enum("paid", "pending", name="pf_status_enum")
    pf_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column("lead_banks", sa.Column("application_id", sa.String(50), nullable=True))
    op.add_column("lead_banks", sa.Column("sanction_date", sa.Date(), nullable=True))
    op.add_column("lead_banks", sa.Column("loan_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("lead_banks", sa.Column("roi", sa.Numeric(5, 2), nullable=True))  # rate, e.g. 11.00
    op.add_column("lead_banks", sa.Column("tenure_months", sa.Integer(), nullable=True))
    op.add_column("lead_banks", sa.Column("pf_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("lead_banks", sa.Column("first_tranche_amount", sa.Numeric(14, 2), nullable=True))
    op.add_column("lead_banks", sa.Column("no_of_tranches", sa.Integer(), nullable=True))
    op.add_column(
        "lead_banks",
        sa.Column(
            "pf_status",
            sa.Enum("paid", "pending", name="pf_status_enum", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    for col in (
        "pf_status", "no_of_tranches", "first_tranche_amount",
        "pf_amount", "tenure_months", "roi", "loan_amount",
        "sanction_date", "application_id",
    ):
        op.drop_column("lead_banks", col)
    op.execute("DROP TYPE IF EXISTS pf_status_enum")
