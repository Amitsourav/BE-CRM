"""Meta Lead Ads form → tenant routing table

Revision ID: y2v3w4x5y6z7
Revises: x1u2v3w4x5y6
Create Date: 2026-05-28

FMC backend acts as the Meta webhook gateway (single Meta app, one
webhook URL). Each ad form is registered here with its target tenant
('fmc' = process locally, 'av' = forward to AV backend) and the
display label / source_id the lead should be tagged with.

Applies to both FMC and AV DBs (single codebase, dual deployments).
The AV deployment will have an empty table and never reads it — harmless.
"""
from alembic import op
import sqlalchemy as sa


revision = "y2v3w4x5y6z7"
down_revision = "x1u2v3w4x5y6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_form_routing",
        sa.Column("form_id", sa.String(50), primary_key=True),
        sa.Column("target", sa.String(10), nullable=False),
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("target IN ('fmc', 'av')", name="meta_form_target_chk"),
    )


def downgrade() -> None:
    op.drop_table("meta_form_routing")
