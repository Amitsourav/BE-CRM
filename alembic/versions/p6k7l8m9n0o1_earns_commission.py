"""bank_disbursements.earns_commission — some tranches earn nothing

Revision ID: p6k7l8m9n0o1
Revises: o5j6k7l8m9n0
Create Date: 2026-08-31

FMC's revenue tracker carries an "Eligible for Commission?" flag on every
row of its disbursement log, and it is not always Yes: a ₹3.22 L
disbursement to PNB Direct sits marked No against a 0.7% rate and earns
zero. Nothing in the model could express that — commission was always
computed from the rate, so a tranche that earns nothing had no way to
say so.

Asked whether a rule decides it, Amit said no: he wants a tick box and
decides case by case. So this is a plain flag with nothing inferring it.

Defaults true, which leaves every existing row exactly as it is.
"""
import sqlalchemy as sa
from alembic import op


revision = "p6k7l8m9n0o1"
down_revision = "o5j6k7l8m9n0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bank_disbursements",
        sa.Column(
            "earns_commission", sa.Boolean(),
            nullable=False, server_default=sa.text("true"),
        ),
    )


def downgrade():
    op.drop_column("bank_disbursements", "earns_commission")
