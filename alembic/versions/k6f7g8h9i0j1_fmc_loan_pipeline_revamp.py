"""FMC pipeline revamp — loan-processing flow + is_important flag

Revision ID: k6f7g8h9i0j1
Revises: j5e6f7g8h9i0
Create Date: 2026-05-07

Replaces the original 6-stage FMC funnel (lead → called → connected →
qualified_lead → won/lost) with a 12-stage loan-processing pipeline:

  Created → Contacted → DNP → Qualified → Processing → Docs Pending →
  Logged In → Sanctioned → PF Paid → Disbursed → Opportunity → Lost

"Important" is NOT a stage — it's a boolean flag (is_important) so a
manager can star any lead in any stage to escalate. The Kanban shows
the star icon on the card, lead doesn't move columns.

DNP behavior (wired in code, not DB): every AI call that fails to
connect auto-moves the lead to DNP and bumps call_attempt_count.
After 12 DNPs, lead auto-moves to Lost.

Free-movement: any agent can move any lead to any non-terminal stage,
mirroring the Admitverse design (counselors don't fight a strict
forward-only state machine).

Existing 5226 leads — all currently at stage 'lead' — are migrated to
'created' (same meaning, renamed). Old enum values (lead, called,
connected, qualified_lead, won) stay in the enum for safety but no
new code path produces them.
"""
from alembic import op
import sqlalchemy as sa


revision = "k6f7g8h9i0j1"
down_revision = "j5e6f7g8h9i0"
branch_labels = None
depends_on = None


# 6 new enum values — created/contacted/qualified/processing/opportunity
# already exist for Admitverse, so we don't re-add those.
NEW_ENUM_VALUES = [
    "dnp",
    "docs_pending",
    "logged_in",
    "sanctioned",
    "pf_paid",
    "disbursed",
]


def upgrade():
    # ALTER TYPE ... ADD VALUE must run outside a transaction in
    # PostgreSQL <12. Alembic wraps everything in a transaction by
    # default. Workaround: commit, run the statement, reopen.
    conn = op.get_bind()
    for value in NEW_ENUM_VALUES:
        conn.execute(sa.text(f"ALTER TYPE lead_stage ADD VALUE IF NOT EXISTS '{value}'"))

    # is_important flag — boolean, default false, indexed for the
    # Kanban "show only important leads" filter and the agent's
    # "important leads queue" view.
    op.add_column(
        "leads",
        sa.Column(
            "is_important",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "idx_leads_is_important",
        "leads",
        ["is_important"],
        postgresql_where=sa.text("is_important = true"),
    )

    # Migrate the 5226 existing FMC leads currently sitting at 'lead'
    # to 'created'. Only touches the FMC tenant — Admitverse leads
    # never used 'lead' as a stage, so this is safe to run on either
    # database.
    conn.execute(sa.text("UPDATE leads SET current_stage = 'created' WHERE current_stage = 'lead'"))


def downgrade():
    op.drop_index("idx_leads_is_important", table_name="leads")
    op.drop_column("leads", "is_important")
    # ENUM values can't easily be dropped in PG <14 and dropping them
    # is destructive anyway — leaving them in the type is harmless.
    # Reverse-migrate the leads if rolling back so they go back to
    # 'lead' and the old code paths still work.
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE leads SET current_stage = 'lead' WHERE current_stage = 'created'"))
