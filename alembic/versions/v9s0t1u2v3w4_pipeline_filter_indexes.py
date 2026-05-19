"""GIN indexes for tags + trigram search — speeds up the slow Kanban filters

Revision ID: v9s0t1u2v3w4
Revises: u8r9s0t1u2v3
Create Date: 2026-05-19

After shipping the Kanban filter set, two filters were measurably slow
on the FMC dataset (6,100 active leads):

  • tags overlap — 89ms server-side (full table scan)
  • search ILIKE %term% on name/phone/email — 13ms today on FMC but
    grows linearly with data volume; on a 50k-lead dataset this becomes
    100-200ms

Both fix cleanly with GIN indexes:
  - tags: standard GIN index on the text[] column
  - search: pg_trgm extension + GIN index on full_name + phone
    (Postgres can then use the index for ILIKE %term% queries instead
    of a sequential scan)
"""
from alembic import op


revision = "v9s0t1u2v3w4"
down_revision = "u8r9s0t1u2v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # GIN index on the tags array — supports the && (overlap) operator
    # used by the Kanban tags filter.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_leads_tags_gin
        ON leads USING gin (tags)
    """)

    # pg_trgm enables trigram-based ILIKE / LIKE acceleration. Idempotent.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram indexes for the search box. Three separate indexes
    # rather than one composite — Postgres OR's the lookups, picks the
    # cheapest path per query.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_leads_full_name_trgm
        ON leads USING gin (full_name gin_trgm_ops)
        WHERE NOT is_deleted
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_leads_phone_trgm
        ON leads USING gin (phone gin_trgm_ops)
        WHERE NOT is_deleted AND phone IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_leads_email_trgm
        ON leads USING gin (email gin_trgm_ops)
        WHERE NOT is_deleted AND email IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_leads_email_trgm")
    op.execute("DROP INDEX IF EXISTS idx_leads_phone_trgm")
    op.execute("DROP INDEX IF EXISTS idx_leads_full_name_trgm")
    op.execute("DROP INDEX IF EXISTS idx_leads_tags_gin")
    # Leave pg_trgm extension installed — harmless.
