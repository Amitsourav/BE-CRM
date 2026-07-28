"""website_submissions inbox + 'website' lead source type

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-07-28

Website lead forms (admitverse.com, fundmycampus.com) POST to
/api/v1/internal/website/ingest. Submissions land in this table as
`status='new'` rather than becoming Leads directly — a human triages
them in the Website Leads panel and converts the real ones.

Two changes, both additive:

1. `lead_source_type` gains a 'website' value so converted submissions
   get a proper source ("Website — AV Contact Form") and show up in the
   existing sources report broken down per form. ADD VALUE cannot run
   inside a transaction, hence the autocommit block. IF NOT EXISTS makes
   it a no-op on re-run (PG 12+).

2. The `website_submissions` table itself.

Nothing existing is altered, so this is safe to apply to a live DB while
the old code is still running.
"""
from alembic import op
import sqlalchemy as sa


revision = "e5f6a7b8c9d0"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New enum value — must be outside a transaction.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE lead_source_type ADD VALUE IF NOT EXISTS 'website'")

    # 2. The inbox table.
    op.create_table(
        "website_submissions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        # Which form fired.
        sa.Column("form_key", sa.String(80), nullable=False),
        sa.Column("form_name", sa.String(120), nullable=True),
        sa.Column("source", sa.String(80), nullable=True),
        sa.Column("page", sa.Text(), nullable=True),
        sa.Column("tag", sa.String(80), nullable=True),
        # The person.
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        # Raw body — so a form can add fields without a backend deploy.
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # Website-side idempotency key.
        sa.Column("external_id", sa.String(100), nullable=True),
        # Triage.
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("lead_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_by", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('new', 'converted', 'duplicate', 'spam')",
            name="website_submission_status_chk",
        ),
    )
    # Panel default view: newest-first within a status, per tenant.
    op.create_index(
        "idx_website_subs_company_status_created",
        "website_submissions",
        ["company_id", "status", "created_at"],
    )
    # "Already in the CRM?" checks at ingest.
    op.create_index("idx_website_subs_company_email", "website_submissions", ["company_id", "email"])
    op.create_index("idx_website_subs_company_phone", "website_submissions", ["company_id", "phone"])
    # Retry idempotency — partial so rows without an external id never collide.
    op.create_index(
        "uniq_website_subs_external_id",
        "website_submissions",
        ["company_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uniq_website_subs_external_id", table_name="website_submissions")
    op.drop_index("idx_website_subs_company_phone", table_name="website_submissions")
    op.drop_index("idx_website_subs_company_email", table_name="website_submissions")
    op.drop_index("idx_website_subs_company_status_created", table_name="website_submissions")
    op.drop_table("website_submissions")
    # NOTE: the 'website' enum value is intentionally NOT removed —
    # Postgres has no DROP VALUE, and rewriting the type would break any
    # lead_sources row already using it.
