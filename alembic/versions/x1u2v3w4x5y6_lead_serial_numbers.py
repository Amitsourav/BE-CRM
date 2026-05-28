"""Per-company serial numbers on leads

Revision ID: x1u2v3w4x5y6
Revises: w0t1u2v3w4x5
Create Date: 2026-05-28

Each tenant sees leads as #1, #2, #3, ... in created order. Used by the
admin "Distribute" flow ("give leads 1-50 to Shivam, 51-100 to Himanshu")
and shown on every Kanban card / lead detail page for quick reference.

Two structures:
  • leads.serial_no INT (nullable for backfill, UNIQUE per company once set)
  • company_lead_counters table — atomic per-tenant counter

Backfill assigns serial numbers in created_at ascending order per
company, then seeds the counter with MAX(serial_no)+1 so new leads
pick up where the backfill left off.
"""
from alembic import op
import sqlalchemy as sa


revision = "x1u2v3w4x5y6"
down_revision = "w0t1u2v3w4x5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Counter table — one row per company, atomic increment.
    op.create_table(
        "company_lead_counters",
        sa.Column("company_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("companies.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("next_serial", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # 2. serial_no column on leads — nullable until backfill completes.
    op.add_column("leads", sa.Column("serial_no", sa.Integer(), nullable=True))

    # 3. Backfill: window-function assign serial in created_at order per company.
    op.execute("""
        WITH numbered AS (
          SELECT
            id,
            ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY created_at ASC, id ASC) AS rn
          FROM leads
        )
        UPDATE leads
        SET serial_no = numbered.rn
        FROM numbered
        WHERE leads.id = numbered.id
    """)

    # 4. Seed the counters with MAX(serial_no) + 1 per company.
    op.execute("""
        INSERT INTO company_lead_counters (company_id, next_serial)
        SELECT company_id, COALESCE(MAX(serial_no), 0) + 1 AS next_serial
        FROM leads
        GROUP BY company_id
        ON CONFLICT (company_id) DO UPDATE
          SET next_serial = EXCLUDED.next_serial
    """)

    # 5. Unique index per company so the FE can do GET /leads?serial=42.
    op.create_index(
        "uniq_leads_serial_per_company",
        "leads",
        ["company_id", "serial_no"],
        unique=True,
        postgresql_where=sa.text("serial_no IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uniq_leads_serial_per_company", table_name="leads")
    op.drop_column("leads", "serial_no")
    op.drop_table("company_lead_counters")
