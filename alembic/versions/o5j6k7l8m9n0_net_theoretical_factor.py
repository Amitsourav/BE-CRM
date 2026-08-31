"""invoice_settings.net_theoretical_factor — the drawdown assumption

Revision ID: o5j6k7l8m9n0
Revises: n4i5j6k7l8m9
Create Date: 2026-08-31

Net theoretical revenue = gross theoretical revenue x this percentage.

Gross theoretical assumes every sanctioned loan draws down in full, which
they do not: students take less than they were approved for, go elsewhere,
or drop out. FMC's standing assumption is that 80% of the gross figure is
what will realistically be earned (Amit, 2026-08-31). One factor for all
lenders, and stored rather than hard-coded because he asked for it to be
changeable.

Default 80.00 so every existing tenant gets the current assumption with no
backfill. Lives on invoice_settings because that is already the
per-company business-settings table — tax rate, invoice numbering, legal
identity — even though the name no longer describes everything it holds.
A tenant with no invoice_settings row at all falls back to 80 in the
service rather than failing.
"""
import sqlalchemy as sa
from alembic import op


revision = "o5j6k7l8m9n0"
down_revision = "n4i5j6k7l8m9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "invoice_settings",
        sa.Column(
            "net_theoretical_factor",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("80.00"),
        ),
    )


def downgrade():
    op.drop_column("invoice_settings", "net_theoretical_factor")
