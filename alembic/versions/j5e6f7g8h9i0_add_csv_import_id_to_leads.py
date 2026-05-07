"""add csv_import_id to leads for bulk-by-CSV campaign assignment

Revision ID: j5e6f7g8h9i0
Revises: i4d5e6f7g8h9
Create Date: 2026-05-07

Lets a campaign creator pick "all leads from CSV upload X" without having
to click 4,500 checkboxes across 225 pages. The bulk-assign-leads endpoint
filters on this column when csv_import_id is supplied.

Existing leads stay NULL — we have no historical mapping to backfill.
created_after/created_before filters cover those leads instead.
"""
from alembic import op
import sqlalchemy as sa


revision = "j5e6f7g8h9i0"
down_revision = "i4d5e6f7g8h9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "leads",
        sa.Column(
            "csv_import_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("csv_imports.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Partial index: only non-NULL rows are interesting for the bulk-assign
    # filter. Keeps the index small and skips the 5,857 existing leads
    # whose csv_import_id is NULL forever.
    op.create_index(
        "idx_leads_csv_import_id",
        "leads",
        ["csv_import_id"],
        postgresql_where=sa.text("csv_import_id IS NOT NULL"),
    )


def downgrade():
    op.drop_index("idx_leads_csv_import_id", table_name="leads")
    op.drop_column("leads", "csv_import_id")
