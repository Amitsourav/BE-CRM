"""add lead_remarks table

Revision ID: q4n5o6p7q8r9
Revises: p3m4n5o6p7q8
Create Date: 2026-05-15

FMC wants a dedicated remarks feed per lead so counsellors and admins
can leave notes side-by-side and read each other's context. Distinct
from the existing `leads.notes` (single-value catch-all) and
`lead_stage_logs.conversation_notes` (tied to a stage transition) —
this is a free-form chronological feed.

Visibility: anyone with access to the lead can read + write. Author
is captured so the FE can label "Posted by Ashmita (Manager) — 2h ago".
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "q4n5o6p7q8r9"
down_revision = "p3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_remarks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", UUID(as_uuid=True), sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True),
        # author_role frozen at write time so reads stay stable even if
        # the author's role changes later.
        sa.Column("author_role", sa.String(50), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_lead_remarks_lead_id", "lead_remarks", ["lead_id"])
    op.create_index("ix_lead_remarks_company_id", "lead_remarks", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_remarks_company_id", table_name="lead_remarks")
    op.drop_index("ix_lead_remarks_lead_id", table_name="lead_remarks")
    op.drop_table("lead_remarks")
