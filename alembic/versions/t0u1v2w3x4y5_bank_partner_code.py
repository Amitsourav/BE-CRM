"""banks.partner_code — the code a lender knows us by

FMC's revenue tracker has a `Bank code` tab holding the DSA/partner code
each lender issued: PNB `PCSLDSA9229`, BOI `BOISL/DEL/202606/EL-…`. It is
what a commission claim is filed under, and it existed nowhere in the
CRM. Closing the sheet without it would have lost the codes entirely.

Nullable: most lenders in the catalogue have never issued one.

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
"""
from alembic import op
import sqlalchemy as sa

revision = "t0u1v2w3x4y5"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("banks", sa.Column("partner_code", sa.String(60), nullable=True))


def downgrade() -> None:
    op.drop_column("banks", "partner_code")
