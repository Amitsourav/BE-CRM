"""Invoice settings + counters + invoices (FMC GST bill maker)

Revision ID: a4b5c6d7e8f9
Revises: z3w4x5y6z7a8
Create Date: 2026-06-09

Adds the 3 tables backing the in-CRM invoice generator:
  • invoice_settings   — one row per tenant (FMC's GSTIN, address, logo, etc.)
  • invoice_counters   — atomic per-(company, FY) sequence counter
  • invoices           — one row per issued invoice (immutable customer
                         snapshot, JSONB line items, money totals,
                         PDF storage path)

The counter table mirrors the pattern in `company_lead_counters` so
concurrent invoice creates are race-safe via INSERT … ON CONFLICT …
RETURNING (same as serial number reservation in lead_service.py).

All money columns are Numeric(14,2) — supports up to 99,999,99,99,999.99
paise-precision (10 lakh crore upper bound).
"""
from alembic import op
import sqlalchemy as sa


revision = "a4b5c6d7e8f9"
down_revision = "z3w4x5y6z7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── invoice_settings (one row per company) ─────────────────────────
    op.create_table(
        "invoice_settings",
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("legal_name", sa.String(200), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=False),
        sa.Column("pan", sa.String(10), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("state_name", sa.String(50), nullable=False),
        sa.Column("address_line1", sa.String(200), nullable=False),
        sa.Column("address_line2", sa.String(200), nullable=True),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("pincode", sa.String(10), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("bank_account_name", sa.String(200), nullable=True),
        sa.Column("bank_account_number", sa.String(50), nullable=True),
        sa.Column("bank_ifsc", sa.String(11), nullable=True),
        sa.Column("bank_name", sa.String(100), nullable=True),
        sa.Column("bank_branch", sa.String(100), nullable=True),
        sa.Column("logo_url", sa.Text, nullable=True),
        sa.Column("signature_url", sa.Text, nullable=True),
        sa.Column("invoice_prefix", sa.String(20), nullable=False, server_default="FMC"),
        sa.Column(
            "default_tax_rate",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="18.00",
        ),
        sa.Column("default_terms", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("char_length(gstin) = 15", name="invoice_settings_gstin_len_chk"),
        sa.CheckConstraint("char_length(state_code) = 2", name="invoice_settings_state_code_len_chk"),
    )

    # ── invoice_counters (composite PK, atomic counter) ────────────────
    op.create_table(
        "invoice_counters",
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("financial_year", sa.String(7), nullable=False),
        sa.Column("next_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("company_id", "financial_year", name="invoice_counters_pkey"),
    )

    # ── invoices ───────────────────────────────────────────────────────
    op.create_table(
        "invoices",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("invoice_number", sa.String(50), nullable=False),
        sa.Column("financial_year", sa.String(7), nullable=False),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        sa.Column("invoice_date", sa.Date, nullable=False),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="issued"),
        # ── Customer snapshot (denormalized at issue time, immutable) ─
        sa.Column("customer_name", sa.String(200), nullable=False),
        sa.Column("customer_gstin", sa.String(15), nullable=True),
        sa.Column("customer_state_code", sa.String(2), nullable=True),
        sa.Column("customer_state_name", sa.String(50), nullable=True),
        sa.Column("customer_email", sa.String(200), nullable=True),
        sa.Column("customer_phone", sa.String(20), nullable=True),
        sa.Column("customer_address", sa.Text, nullable=True),
        sa.Column(
            "lead_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # ── Money totals ──────────────────────────────────────────────
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
        sa.Column("cgst_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("sgst_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("igst_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_tax", sa.Numeric(14, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(14, 2), nullable=False),
        sa.Column("tax_rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("tax_split", sa.String(10), nullable=False),
        # ── Line items + audit ───────────────────────────────────────
        sa.Column(
            "line_items",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("pdf_url", sa.Text, nullable=True),
        sa.Column("pdf_storage_path", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("terms", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('draft','issued','paid','void')",
            name="invoices_status_chk",
        ),
        sa.CheckConstraint(
            "tax_split IN ('cgst_sgst','igst')",
            name="invoices_tax_split_chk",
        ),
    )

    # Indexes
    op.create_index(
        "uniq_invoices_number_per_company",
        "invoices",
        ["company_id", "invoice_number"],
        unique=True,
    )
    op.create_index(
        "uniq_invoices_seq_per_fy",
        "invoices",
        ["company_id", "financial_year", "sequence_number"],
        unique=True,
    )
    op.create_index(
        "idx_invoices_company_date",
        "invoices",
        ["company_id", sa.text("invoice_date DESC")],
    )
    op.create_index(
        "idx_invoices_company_status",
        "invoices",
        ["company_id", "status"],
    )
    op.create_index(
        "idx_invoices_lead",
        "invoices",
        ["lead_id"],
        postgresql_where=sa.text("lead_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_invoices_lead", table_name="invoices")
    op.drop_index("idx_invoices_company_status", table_name="invoices")
    op.drop_index("idx_invoices_company_date", table_name="invoices")
    op.drop_index("uniq_invoices_seq_per_fy", table_name="invoices")
    op.drop_index("uniq_invoices_number_per_company", table_name="invoices")
    op.drop_table("invoices")
    op.drop_table("invoice_counters")
    op.drop_table("invoice_settings")
