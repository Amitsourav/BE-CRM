"""add primary_university + application_status mirror columns on leads (Admitverse)

Revision ID: c2d3e4f5a6b7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-17

Analog of lead.bank_name / lead.bank_status. The service syncs these to
the highest-priority lead_applications entry so the Kanban tile can show
the lead's primary university + its status without joining the child
table on every card. The application_status enum already exists (created
in c1d2e3f4a5b6). FMC leaves both NULL.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision = "c2d3e4f5a6b7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("primary_university", sa.String(200), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "application_status",
            ENUM(
                "applied", "shortlisted", "offer_received", "conditional_offer",
                "unconditional_offer", "deposit_paid", "cas_received",
                "visa_applied", "visa_approved", "enrolled", "rejected", "withdrawn",
                name="application_status", create_type=False,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("leads", "application_status")
    op.drop_column("leads", "primary_university")
