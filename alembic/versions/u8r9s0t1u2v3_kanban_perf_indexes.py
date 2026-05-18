"""Indexes for the slow Kanban queries — drops /by-stage from ~4s to ~1s

Revision ID: u8r9s0t1u2v3
Revises: t7q8r9s0t1u2
Create Date: 2026-05-18

Two queries dominated the Pipeline load time (measured against FMC's
6,100-lead dataset):

  • Main items+window — 1945ms. The ROW_NUMBER() OVER (PARTITION BY
    current_stage ORDER BY created_at DESC) had to scan all 6,100 rows
    because no single index covered (company_id, current_stage,
    created_at) WHERE NOT is_deleted.

  • Latest stage-log notes — 1203ms. The DISTINCT ON (lead_id) +
    ORDER BY created_at DESC was hitting the existing
    (lead_id, created_at DESC) index but still had to filter
    conversation_notes IS NOT NULL row-by-row.

These two indexes are partial (cover only the rows the Kanban queries
hit) so they stay small and fast to maintain. The first one also acts
as a covering index for the per-stage count query.
"""
from alembic import op


revision = "u8r9s0t1u2v3"
down_revision = "t7q8r9s0t1u2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Composite partial index — speeds up the main Kanban window function
    # AND the per-stage count query. Both filter on (company_id,
    # is_deleted=false) and want rows ordered by created_at desc within
    # each current_stage.
    op.execute("""
        CREATE INDEX idx_leads_kanban_window
        ON leads (company_id, current_stage, created_at DESC)
        WHERE NOT is_deleted
    """)

    # Partial index on stage logs — skips the rows without notes
    # entirely, which is the vast majority. Speeds up both the "latest
    # note" DISTINCT ON and the notes_count aggregation.
    op.execute("""
        CREATE INDEX idx_stage_logs_lead_with_notes
        ON lead_stage_logs (lead_id, created_at DESC)
        WHERE conversation_notes IS NOT NULL AND length(conversation_notes) > 0
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_stage_logs_lead_with_notes")
    op.execute("DROP INDEX IF EXISTS idx_leads_kanban_window")
