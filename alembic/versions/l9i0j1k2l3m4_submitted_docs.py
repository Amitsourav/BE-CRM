"""add submitted_docs array on leads (per-doc checklist tracking)

Revision ID: l9i0j1k2l3m4
Revises: l8h9i0j1k2l3
Create Date: 2026-05-08

The Kanban tile previously tracked "X / 6 docs" as opaque counters
(docs_submitted, docs_required). Telecallers couldn't see WHICH docs
were pending — Aadhaar? PAN? ITR? — without opening the lead.

submitted_docs stores the keys of docs the lead has handed in.
Auto-syncs docs_submitted = len(submitted_docs) on update so
existing widgets reading the counter keep working unchanged.

Standard FMC checklist (defined in app/core/constants.py):
  aadhaar, pan, academic, offer_letter, financial, itr
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


revision = "l9i0j1k2l3m4"
down_revision = "l8h9i0j1k2l3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "leads",
        sa.Column(
            "submitted_docs",
            ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )


def downgrade():
    op.drop_column("leads", "submitted_docs")
