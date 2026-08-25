"""bank_status gains 'lost' — a lender can reject a file

Revision ID: l2g3h4i5j6k7
Revises: k1f2a3b4c5d6
Create Date: 2026-08-25

The per-bank status could describe a file moving forward (applied ->
loan_login -> sanctioned -> pf_paid -> disbursed) but had no way to say
a lender said no. That outcome was being recorded either by deleting the
row — destroying the fact that the lender was ever approached, and the
WhatsApp conversation attached to it — or by leaving the cell at
'applied' forever, which reads as "still waiting" and keeps the file in
everyone's follow-up list.

'lost' here is per-LENDER and says nothing about the lead: PNB declining
is routine while Axis is still processing, and the lead's own
current_stage is untouched by it. It sorts BELOW 'applied' in
LeadService._BANK_STATUS_PRIORITY for exactly that reason — a bank that
rejected the file must never be lifted onto lead.bank_name as the lead's
primary lender.

Appended at the end of the enum rather than positioned before 'applied':
enum order only affects raw ORDER BY, which nothing does — every ranking
goes through the priority table in the service.

Note ALTER TYPE ... ADD VALUE cannot be reversed; Postgres has no
DROP VALUE. The downgrade is therefore a no-op, and rolling back past
this revision leaves the label in place (harmless — nothing writes it).
"""
from alembic import op


revision = "l2g3h4i5j6k7"
down_revision = "k1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    # IF NOT EXISTS so a re-run (or a fresh DB already bootstrapped from
    # the model definition) is a no-op rather than an error. Safe inside
    # alembic's transaction on PG 12+ because the value is added here and
    # not USED until a later transaction.
    op.execute("ALTER TYPE bank_status ADD VALUE IF NOT EXISTS 'lost'")


def downgrade():
    # Postgres cannot remove a value from an enum type. Rows using it
    # would have to be rewritten and the type recreated; not worth it for
    # a label nothing depends on structurally.
    pass
