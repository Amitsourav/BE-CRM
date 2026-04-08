"""redesign_calls_table_for_telephony

Enhances call_attempts table for Bolna AI telephony:
- Adds new columns: telecaller_id, sentiment_score, cost, updated_at
- Sets NOT NULL + defaults on call_type and call_status
  (previously nullable from migration 34a3d5d2994b)
- Adds FK from ai_agent_id to ai_agents table
- Adds performance indexes
- Backfills existing rows: call_type='ai', call_status='ended'

Does NOT drop any existing columns.

Revision ID: 2aac85e3cee8
Revises: 2f7f3ccdaf76
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2aac85e3cee8'
down_revision: Union[str, None] = '2f7f3ccdaf76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- New columns ---
    op.add_column('call_attempts', sa.Column('telecaller_id', sa.UUID(), nullable=True))
    op.add_column('call_attempts', sa.Column('sentiment_score', sa.Float(), nullable=True))
    op.add_column('call_attempts', sa.Column('cost', sa.Float(), nullable=True))
    op.add_column('call_attempts', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))

    # --- Backfill existing rows before setting NOT NULL ---
    op.execute("UPDATE call_attempts SET call_type = 'ai' WHERE call_type IS NULL")
    op.execute("UPDATE call_attempts SET call_status = 'ended' WHERE call_status IS NULL")

    # --- Set call_type: NOT NULL with default ---
    op.alter_column('call_attempts', 'call_type',
                    existing_type=sa.String(),
                    type_=sa.String(20),
                    nullable=False,
                    server_default=sa.text("'ai'"))

    # --- Set call_status: NOT NULL with default ---
    op.alter_column('call_attempts', 'call_status',
                    existing_type=sa.String(),
                    type_=sa.String(20),
                    nullable=False,
                    server_default=sa.text("'pending'"))

    # --- Add FK for ai_agent_id → ai_agents ---
    op.create_foreign_key(
        'fk_call_attempts_ai_agent_id',
        'call_attempts', 'ai_agents',
        ['ai_agent_id'], ['id'],
        ondelete='SET NULL',
    )

    # --- Add FK for telecaller_id → profiles ---
    op.create_foreign_key(
        'fk_call_attempts_telecaller_id',
        'call_attempts', 'profiles',
        ['telecaller_id'], ['id'],
        ondelete='SET NULL',
    )

    # --- Drop old index from migration 34a3d5d2994b (provider index) ---
    # It was on bolna_call_id — we'll recreate with consistent naming
    op.drop_index('ix_call_attempts_bolna_call_id', table_name='call_attempts')

    # --- Add performance indexes ---
    op.create_index('ix_call_attempts_call_status', 'call_attempts', ['call_status'])
    op.create_index('ix_call_attempts_call_type', 'call_attempts', ['call_type'])
    op.create_index('ix_call_attempts_provider', 'call_attempts', ['bolna_call_id'])
    op.create_index('ix_call_attempts_ai_agent', 'call_attempts', ['ai_agent_id'])
    op.create_index('ix_call_attempts_telecaller', 'call_attempts', ['telecaller_id'])
    op.create_index('ix_call_attempts_sentiment', 'call_attempts', ['sentiment'])
    op.create_index('ix_call_attempts_started_at', 'call_attempts', ['started_at'])


def downgrade() -> None:
    # Drop new indexes
    op.drop_index('ix_call_attempts_started_at', table_name='call_attempts')
    op.drop_index('ix_call_attempts_sentiment', table_name='call_attempts')
    op.drop_index('ix_call_attempts_telecaller', table_name='call_attempts')
    op.drop_index('ix_call_attempts_ai_agent', table_name='call_attempts')
    op.drop_index('ix_call_attempts_provider', table_name='call_attempts')
    op.drop_index('ix_call_attempts_call_type', table_name='call_attempts')
    op.drop_index('ix_call_attempts_call_status', table_name='call_attempts')

    # Restore old index
    op.create_index('ix_call_attempts_bolna_call_id', 'call_attempts', ['bolna_call_id'])

    # Drop FKs
    op.drop_constraint('fk_call_attempts_telecaller_id', 'call_attempts', type_='foreignkey')
    op.drop_constraint('fk_call_attempts_ai_agent_id', 'call_attempts', type_='foreignkey')

    # Revert call_type and call_status to nullable
    op.alter_column('call_attempts', 'call_status',
                    existing_type=sa.String(20),
                    nullable=True,
                    server_default=None)
    op.alter_column('call_attempts', 'call_type',
                    existing_type=sa.String(20),
                    nullable=True,
                    server_default=None)

    # Drop new columns
    op.drop_column('call_attempts', 'updated_at')
    op.drop_column('call_attempts', 'cost')
    op.drop_column('call_attempts', 'sentiment_score')
    op.drop_column('call_attempts', 'telecaller_id')
