"""add lead_stage_logs changed_by index

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-21

Audit queries like "show every stage change by user X" currently full-scan
lead_stage_logs. Add an index on changed_by so the Activity / History tabs
stay fast as the table grows.
"""
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "5a044326b9cf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_lead_stage_logs_changed_by",
        "lead_stage_logs",
        ["changed_by"],
    )


def downgrade() -> None:
    op.drop_index("idx_lead_stage_logs_changed_by", table_name="lead_stage_logs")
