"""bank share provenance on lead_banks + lead_bank_messages

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-08-07

Phase two of the WhatsApp integration: record that a lead's file was
shared into a lender's WhatsApp group, and keep the conversation that
follows against that specific (lead, bank) pair.

Extends `lead_banks` rather than adding a parallel table. That table is
already exactly one row per (lead, bank) — enforced by
uniq_lead_banks_lead_bank — and its own docstring describes it as
tracking a lead "shared with a specific bank". A second structure
recording the same relationship would drift from this one.

`bank_status` is untouched. Share provenance (who put the file in front
of the bank, and when) is a different fact from the bank's decision
about it.

Backfill note: the 436 pre-existing rows were created through the UI
with no provenance captured. shared_at is set from created_at and source
to 'manual'. That is an INFERENCE — a lead_banks row has always been
created at the point someone put the lead to that bank — and it is
applied only where shared_at IS NULL, so it cannot overwrite real data.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "i9d0e1f2a3b4"
down_revision = "h8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_banks", sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lead_banks", sa.Column("shared_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("lead_banks", sa.Column("source", sa.String(length=30), nullable=True))
    op.add_column("lead_banks", sa.Column("wa_group_id", sa.String(length=120), nullable=True))
    op.create_foreign_key(
        "fk_lead_banks_shared_by", "lead_banks", "profiles",
        ["shared_by"], ["id"], ondelete="SET NULL",
    )

    # Inferred, and guarded so a re-run can't clobber real values.
    op.execute("""
        UPDATE lead_banks
           SET shared_at = created_at,
               source    = 'manual'
         WHERE shared_at IS NULL
    """)

    op.create_table(
        "lead_bank_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sender_phone", sa.String(length=32), nullable=True),
        sa.Column("sender_name", sa.String(length=120), nullable=True),
        sa.Column("is_our_team", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("wa_message_id", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_bank_id"], ["lead_banks.id"], ondelete="CASCADE"),
    )
    # Drives every read: "the conversation for this cell, oldest first".
    op.create_index(
        "ix_lead_bank_messages_lead_bank_id",
        "lead_bank_messages", ["lead_bank_id", "created_at"],
    )
    # Idempotency for the bot. PARTIAL so the many UI-added messages,
    # which have no WhatsApp id, don't all collide on NULL.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_lead_bank_messages_wa_message_id
          ON lead_bank_messages (wa_message_id)
          WHERE wa_message_id IS NOT NULL
    """)
    # The grid reads shares for a page of leads at a time.
    op.create_index("ix_lead_banks_shared_at", "lead_banks", ["company_id", "shared_at"])


def downgrade() -> None:
    op.drop_index("ix_lead_banks_shared_at", table_name="lead_banks")
    op.execute("DROP INDEX IF EXISTS uniq_lead_bank_messages_wa_message_id")
    op.drop_index("ix_lead_bank_messages_lead_bank_id", table_name="lead_bank_messages")
    op.drop_table("lead_bank_messages")
    op.drop_constraint("fk_lead_banks_shared_by", "lead_banks", type_="foreignkey")
    op.drop_column("lead_banks", "wa_group_id")
    op.drop_column("lead_banks", "source")
    op.drop_column("lead_banks", "shared_by")
    op.drop_column("lead_banks", "shared_at")
