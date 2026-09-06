"""bank_disbursements.disbursed_on_estimated

FMC's revenue tracker left the disbursement date blank on 36 of its 124
release rows — a quarter of the book by value. The amount, the
commission and the outstanding balance are all real; only the date is
missing, and it is missing in the SHEET, so there is nothing to import.

Most of those dates can be pinned from other columns in the same
workbook: the invoice date on the row (money cannot be billed before it
leaves the bank), the receipt date, or the disbursement month. That gives
a defensible LATEST-POSSIBLE date rather than a fact.

Writing such a date without saying so would be worse than leaving it
blank: ageing would present it with the same confidence as a real one and
nobody would ever know which figures to distrust. This flag is what makes
the recovery honest — reports can bucket the row and still say the day is
an estimate.

False for every existing row, and for anything captured through the API,
which requires a real date.

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
"""
from alembic import op
import sqlalchemy as sa

revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bank_disbursements",
        sa.Column(
            "disbursed_on_estimated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bank_disbursements", "disbursed_on_estimated")
