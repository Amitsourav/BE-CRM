"""add invoice_start_number to invoice_settings

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-13

Configurable starting number for a fresh (company, financial-year) invoice
sequence. The first invoice of a new FY / tenant is numbered this value
instead of 1 (default 20) so early invoices don't read as "001". An
in-progress FY is unaffected — the atomic counter only increments once its
row exists. NOT NULL DEFAULT 20 backfills every existing settings row to 20.

Uses IF NOT EXISTS so the column can be pre-applied out-of-band (e.g. a
manual hotfix) without this migration erroring on redeploy.
"""
from alembic import op


revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE invoice_settings "
        "ADD COLUMN IF NOT EXISTS invoice_start_number integer NOT NULL DEFAULT 20"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE invoice_settings DROP COLUMN IF EXISTS invoice_start_number")
