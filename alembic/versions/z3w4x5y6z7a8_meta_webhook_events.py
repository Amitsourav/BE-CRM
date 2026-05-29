"""Meta webhook event queue — persists every payload for retry/audit

Revision ID: z3w4x5y6z7a8
Revises: y2v3w4x5y6z7
Create Date: 2026-05-29

Without this, when the webhook handler returns 200 to Meta and then
background processing fails (AV is down, network blip, exception),
the lead is lost permanently — Meta won't retry because it already
got the 200.

The queue table captures the raw payload + per-event metadata:
  • status: pending / processing / done / failed
  • attempts + last_attempt_at / next_attempt_at for exponential backoff
  • last_error: human-readable error from the last failed attempt
  • target / form_id: extracted on insert so a small admin UI can
    triage stuck rows without parsing JSON

Background worker (next commit) picks up pending rows whose
next_attempt_at has passed. Up to 6 attempts spread over ~6 hours
(matches Meta's 36-hour retry window — by the time we give up,
Meta would have given up anyway).
"""
from alembic import op
import sqlalchemy as sa


revision = "z3w4x5y6z7a8"
down_revision = "y2v3w4x5y6z7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "meta_webhook_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # Identification — pulled out of the payload on insert so admin
        # can filter without JSON parsing.
        sa.Column("leadgen_id", sa.String(50), nullable=True),
        sa.Column("form_id", sa.String(50), nullable=True),
        sa.Column("page_id", sa.String(50), nullable=True),
        # Raw payload exactly as Meta sent it — survives schema changes
        # on Meta's side and gives us a forensic record.
        sa.Column("raw_payload", sa.dialects.postgresql.JSONB, nullable=False),
        # Routing snapshot at insert time, if known (NULL if no routing
        # row exists for the form_id — admin can fix routing then retry).
        sa.Column("target", sa.String(10), nullable=True),
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        # Lifecycle
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_error", sa.Text(), nullable=True),
        # Result: lead_id once successfully ingested locally; remote
        # status string if forwarded to AV (e.g. "ok" / "duplicate").
        sa.Column("resulting_lead_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed', 'dropped')",
            name="meta_event_status_chk",
        ),
    )
    # Picker query is "WHERE status='pending' AND next_attempt_at <= now() ORDER BY next_attempt_at"
    op.create_index(
        "idx_meta_events_pending_due",
        "meta_webhook_events",
        ["status", "next_attempt_at"],
    )
    # Dedup lookup: same leadgen_id shouldn't be processed twice
    op.create_index(
        "uniq_meta_events_leadgen",
        "meta_webhook_events",
        ["leadgen_id"],
        unique=True,
        postgresql_where=sa.text("leadgen_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uniq_meta_events_leadgen", table_name="meta_webhook_events")
    op.drop_index("idx_meta_events_pending_due", table_name="meta_webhook_events")
    op.drop_table("meta_webhook_events")
