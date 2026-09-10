"""WhatsApp conversation on the lead + idempotent remarks

Revision ID: y6z7a8b9c0d1
Revises: x5y6z7a8b9c0
Create Date: 2026-09-10

Iconiq's WhatsApp bot chats with prospects directly and needs somewhere
to put the conversation. Two changes, both additive.

1. `lead_messages` — the direct-to-lead counterpart of
   `lead_bank_messages`. That table hangs off (lead, bank) to keep one
   lender's thread apart from another's; here there is a single
   counterparty, so it hangs off the lead.

   Not `lead_remarks`, because a remark is a counsellor's internal note:
   one author who is a CRM user, no direction, no phone number. A chat
   has two sides and the far side will never have a CRM profile.

   `wa_message_id` is the idempotency key, UNIQUE per lead via a partial
   index. When the bot's request times out it cannot tell whether the
   write landed, so it retries — and without a key that is how the same
   conversation ends up on a lead twice.

2. `lead_remarks.wa_message_id` — the same key on remarks. The bot is
   already sending this field and it is currently ignored, so it is
   already writing duplicate transcripts on every retry. This stops that
   today, without waiting for the bot to move to /messages.

Both are additive and nullable, so FMC and Admitverse gain a table
nothing writes to and a column that stays NULL.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "y6z7a8b9c0d1"
down_revision = "x5y6z7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sender_phone", sa.String(32), nullable=True),
        sa.Column("sender_name", sa.String(120), nullable=True),
        sa.Column("is_our_team", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("wa_message_id", sa.String(160), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    # The thread view: every message for one lead, oldest first.
    op.create_index(
        "idx_lead_messages_lead_time", "lead_messages",
        ["lead_id", "created_at"],
    )
    # The conversation list across leads.
    op.create_index(
        "idx_lead_messages_company_time", "lead_messages",
        ["company_id", "created_at"],
    )
    # Idempotency. Partial, so hand-typed messages (no WhatsApp id) are
    # not forced to collide on NULL.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_lead_messages_wa_id
          ON lead_messages (lead_id, wa_message_id)
          WHERE wa_message_id IS NOT NULL
    """)
    # Finding the lead a WhatsApp number belongs to.
    op.create_index(
        "idx_lead_messages_sender_phone", "lead_messages",
        ["company_id", "sender_phone"],
    )

    # 2. Same key on remarks, for what the bot already sends today.
    op.add_column("lead_remarks", sa.Column("wa_message_id", sa.String(160), nullable=True))
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_lead_remarks_wa_id
          ON lead_remarks (lead_id, wa_message_id)
          WHERE wa_message_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uniq_lead_remarks_wa_id")
    op.drop_column("lead_remarks", "wa_message_id")
    op.execute("DROP INDEX IF EXISTS uniq_lead_messages_wa_id")
    op.drop_index("idx_lead_messages_sender_phone", table_name="lead_messages")
    op.drop_index("idx_lead_messages_company_time", table_name="lead_messages")
    op.drop_index("idx_lead_messages_lead_time", table_name="lead_messages")
    op.drop_table("lead_messages")
