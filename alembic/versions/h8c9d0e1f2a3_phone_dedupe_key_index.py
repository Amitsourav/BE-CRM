"""functional index on the phone dedupe key

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-08-05

Duplicate checks now compare the 10 national digits of a phone rather
than the stored string, so a lead saved as "7004428198" is matched by an
incoming "+917004428198". Without this index that comparison is a
sequential scan on every lead create — on the hot path of a backend
already fighting 2-20s Supabase-Korea latency.

The expression MUST stay character-identical to the one built by
`phone_match_clause` in app/utils/csv_parser.py, or the planner will not
use the index.

Both functions are IMMUTABLE (4-arg regexp_replace and right), which is
what makes a functional index legal here.

Index only, no data touched. The existing uniq_leads_phone_active on the
raw column stays — it still catches exact-format collisions, and this
one does not replace it.
"""
from alembic import op


revision = "h8c9d0e1f2a3"
down_revision = "g7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_leads_phone_dedupe_key
          ON leads (company_id, right(regexp_replace(phone, '[^0-9]', '', 'g'), 10))
          WHERE NOT is_deleted AND phone IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_leads_phone_dedupe_key")
