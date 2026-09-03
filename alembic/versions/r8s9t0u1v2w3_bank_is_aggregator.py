"""banks.is_aggregator — mark the routes that front other lenders

UniCred and Nomad each sit in front of several banks at different rates
(UC Axis 1.00%, Axis Direct (UC Code) 1.35%, UC PNB 0.70%, Nomad Normal
1.60%, Nomad US 3.00%...). A single rate on the parent row is therefore
meaningless, and a file left on the bare name can never have its
commission computed.

Until now the frontend inferred this by checking whether a lender's name
appeared as a word inside other lenders' names. That happens to work
today and breaks the first time a sub-product is renamed, so the fact is
recorded explicitly instead.

Revision ID: r8s9t0u1v2w3
Revises: q7l8m9n0o1p2
"""
from alembic import op
import sqlalchemy as sa

revision = "r8s9t0u1v2w3"
down_revision = "q7l8m9n0o1p2"
branch_labels = None
depends_on = None

# The three bare names in FMC's list that are routes rather than payers.
# Chosen by hand, not by pattern: each has sub-products carrying the real
# rate, and each currently sits on live files with no rate of its own.
_AGGREGATORS = ("UniCred", "Nomad", "Axis")


def upgrade() -> None:
    op.add_column(
        "banks",
        sa.Column(
            "is_aggregator",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE banks SET is_aggregator = true WHERE name IN :names"
        ).bindparams(sa.bindparam("names", _AGGREGATORS, expanding=True))
    )


def downgrade() -> None:
    op.drop_column("banks", "is_aggregator")
