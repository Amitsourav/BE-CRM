"""add lead_applications table for per-university application tracking (Admitverse)

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-06-17

Admitverse counsellors apply each lead to several universities at once,
each at a different status (Oxford offer_received, UCL applied, ...). The
single primary_university + application_status on leads can't express
that. Add a child table — the study-abroad analog of lead_banks. The two
new ENUM types are created idempotently (DO/EXCEPTION) to match the
bootstrap convention since the models declare them create_type=False.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ENUM


revision = "c1d2e3f4a5b6"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE application_status AS ENUM (
                'applied','shortlisted','offer_received','conditional_offer',
                'unconditional_offer','deposit_paid','cas_received',
                'visa_applied','visa_approved','enrolled','rejected','withdrawn'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE visa_status_enum AS ENUM (
                'not_started','applied','approved','rejected'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
        """
    )

    op.create_table(
        "lead_applications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", UUID(as_uuid=True), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("university_name", sa.String(200), nullable=False),
        sa.Column("program", sa.String(200), nullable=True),
        sa.Column("intake", sa.String(50), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column(
            "application_status",
            ENUM(
                "applied", "shortlisted", "offer_received", "conditional_offer",
                "unconditional_offer", "deposit_paid", "cas_received",
                "visa_applied", "visa_approved", "enrolled", "rejected", "withdrawn",
                name="application_status", create_type=False,
            ),
            nullable=False, server_default=sa.text("'applied'"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("application_ref", sa.String(100), nullable=True),
        sa.Column("offer_date", sa.Date(), nullable=True),
        sa.Column("tuition_fee", sa.Numeric(14, 2), nullable=True),
        sa.Column("scholarship_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("deposit_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("deposit_paid_date", sa.Date(), nullable=True),
        sa.Column("cas_number", sa.String(100), nullable=True),
        sa.Column(
            "visa_status",
            ENUM("not_started", "applied", "approved", "rejected", name="visa_status_enum", create_type=False),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("lead_id", "university_name", "program", name="uniq_lead_apps_lead_uni_program"),
    )
    op.create_index("ix_lead_applications_lead_id", "lead_applications", ["lead_id"])
    op.create_index("ix_lead_applications_company_id", "lead_applications", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_lead_applications_company_id", table_name="lead_applications")
    op.drop_index("ix_lead_applications_lead_id", table_name="lead_applications")
    op.drop_table("lead_applications")
    op.execute("DROP TYPE IF EXISTS visa_status_enum")
    op.execute("DROP TYPE IF EXISTS application_status")
