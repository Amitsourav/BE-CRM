"""partial unique indexes on leads(phone) and leads(email) per tenant

Revision ID: r5o6p7q8r9s0
Revises: q4n5o6p7q8r9
Create Date: 2026-05-16

Two-layer dedup so the manual Add Lead form can't create duplicates:
  1. Service-level check in lead_service.create_lead (raises 400 with a
     readable error)
  2. DB-level partial unique index as a backstop for race conditions
     (rapid double-click, two browser tabs, etc.)

WHERE NOT is_deleted so soft-deleting a lead frees up the phone/email
for re-use. Phone unique is case-sensitive; email unique is lowercased
to catch "Foo@x.com" vs "foo@x.com".
"""
from alembic import op


revision = "r5o6p7q8r9s0"
down_revision = "q4n5o6p7q8r9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_leads_phone_active
          ON leads (company_id, phone)
          WHERE NOT is_deleted AND phone IS NOT NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_leads_email_active
          ON leads (company_id, lower(email))
          WHERE NOT is_deleted AND email IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uniq_leads_phone_active")
    op.execute("DROP INDEX IF EXISTS uniq_leads_email_active")
