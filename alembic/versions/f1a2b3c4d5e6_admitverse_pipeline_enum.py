"""add 17 admitverse pipeline values to lead_stage enum

Revision ID: f1a2b3c4d5e6
Revises: e0a1b2c3d4e5
Create Date: 2026-05-05

Admitverse uses a 17-stage admissions pipeline. Existing lead_stage enum
has FMC's 6 values (lead, called, connected, qualified_lead, won, lost);
two of those (connected, lost) are reused by Admitverse. We add the
remaining 17 values so both brands share the same enum, gated at the
service layer by company.slug.

Enum values added: created, contacted, dnp_pre_qualified, qualified,
opportunity, dnp_post_qualified, processing, important,
partial_docs_collected, docs_collected, application_done,
conditional_draft, ucol, deposit_paid, cas_received, visa_applied,
enrolled.
"""
from alembic import op


revision = "f1a2b3c4d5e6"
down_revision = "e0a1b2c3d4e5"
branch_labels = None
depends_on = None


NEW_VALUES = [
    "created",
    "contacted",
    "dnp_pre_qualified",
    "qualified",
    "opportunity",
    "dnp_post_qualified",
    "processing",
    "important",
    "partial_docs_collected",
    "docs_collected",
    "application_done",
    "conditional_draft",
    "ucol",
    "deposit_paid",
    "cas_received",
    "visa_applied",
    "enrolled",
]


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE works inside a transaction on PG 12+.
    # IF NOT EXISTS makes the migration idempotent — safe to re-run.
    for value in NEW_VALUES:
        op.execute(
            f"ALTER TYPE lead_stage ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # Postgres does not support removing enum values directly. A real
    # downgrade would require recreating the type and rewriting every
    # column that references it — not worth the risk for this branch.
    raise NotImplementedError(
        "Removing enum values requires recreating lead_stage; not supported."
    )
