"""banks: the tax details needed to raise a commission invoice

A GST invoice needs the customer's legal name, GSTIN and state — the
state decides CGST+SGST (same state) against IGST (different), and
`derive_customer_state_code` reads it out of the GSTIN. The `banks` table
held none of it, which is why `POST /disbursements/{id}/invoice` could
not exist and why every one of FMC's 126 tranches reads `to_bill`: the
`billed` state was unreachable through the API and `invoice_id` had only
ever been set by a direct database write.

All nullable. A lender without a GSTIN simply cannot be invoiced yet, and
the endpoint says which field is missing rather than issuing a wrong tax
split. Only the ~12 lenders that actually pay commission matter first.

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
"""
from alembic import op
import sqlalchemy as sa

revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("banks", sa.Column("gstin", sa.String(15), nullable=True))
    op.add_column("banks", sa.Column("state_code", sa.String(2), nullable=True))
    op.add_column("banks", sa.Column("billing_name", sa.String(200), nullable=True))
    op.add_column("banks", sa.Column("billing_address", sa.Text(), nullable=True))
    op.add_column("banks", sa.Column("billing_email", sa.String(255), nullable=True))


def downgrade() -> None:
    for c in ("billing_email", "billing_address", "billing_name",
              "state_code", "gstin"):
        op.drop_column("banks", c)
