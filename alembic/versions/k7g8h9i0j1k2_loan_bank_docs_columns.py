"""FMC enhanced tile fields — loan_amount, bank_status, docs_required, docs_submitted

Revision ID: k7g8h9i0j1k2
Revises: k6f7g8h9i0j1
Create Date: 2026-05-08

Backs the FMC enhanced Kanban tile (loan amount, bank application
status, document checklist count). Admitverse can ignore these
columns; FE applies them only on the FMC-branded tile.

bank_status enum mirrors the bank's loan-application stages —
intentionally NOT the same as `lead_stage` (which tracks the CRM
funnel). A lead at stage='processing' might be at bank_status='applied'
or further along; tracking them separately avoids forcing a 1:1
mapping that doesn't exist in the real loan workflow.

loan_amount is free text — '25 L', '2.5 cr', '500000', whatever the
telecaller writes. Storing as VARCHAR keeps the entry frictionless;
parsing/normalizing can come later if reports need it.
"""
from alembic import op
import sqlalchemy as sa


revision = "k7g8h9i0j1k2"
down_revision = "k6f7g8h9i0j1"
branch_labels = None
depends_on = None


BANK_STATUS_VALUES = (
    "applied",
    "docs_reviewed",
    "under_review",
    "loan_login",
    "sanctioned",
    "pf_paid",
    "disbursed",
)


def upgrade():
    # Create bank_status enum type. CREATE TYPE is idempotent here via
    # the DO/EXCEPTION block — if the type already exists from a prior
    # partial run, we don't blow up.
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE bank_status AS ENUM (
                'applied','docs_reviewed','under_review','loan_login',
                'sanctioned','pf_paid','disbursed'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    op.add_column(
        "leads",
        sa.Column("loan_amount", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "bank_status",
            sa.dialects.postgresql.ENUM(
                *BANK_STATUS_VALUES, name="bank_status", create_type=False,
            ),
            nullable=True,
        ),
    )
    # Default checklist size = 6 (Aadhaar, PAN, Academic, Offer letter,
    # Financial, ITR). Per-tenant override later if needed.
    op.add_column(
        "leads",
        sa.Column("docs_required", sa.Integer(), nullable=False, server_default=sa.text("6")),
    )
    op.add_column(
        "leads",
        sa.Column("docs_submitted", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade():
    op.drop_column("leads", "docs_submitted")
    op.drop_column("leads", "docs_required")
    op.drop_column("leads", "bank_status")
    op.drop_column("leads", "loan_amount")
    op.execute("DROP TYPE IF EXISTS bank_status")
