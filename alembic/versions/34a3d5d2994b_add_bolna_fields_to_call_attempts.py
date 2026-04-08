"""add_bolna_fields_to_call_attempts

Adds nullable Bolna AI / telephony fields to call_attempts table:
bolna_call_id, call_status, transcript, summary, sentiment,
started_at, ended_at, ai_agent_id, call_type.

All nullable — existing call data is unaffected.

Revision ID: 34a3d5d2994b
Revises: 7bd41da014a8
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '34a3d5d2994b'
down_revision: Union[str, None] = '7bd41da014a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('call_attempts', sa.Column('bolna_call_id', sa.String(), nullable=True))
    op.add_column('call_attempts', sa.Column('call_status', sa.String(), nullable=True))
    op.add_column('call_attempts', sa.Column('transcript', sa.Text(), nullable=True))
    op.add_column('call_attempts', sa.Column('summary', sa.Text(), nullable=True))
    op.add_column('call_attempts', sa.Column('sentiment', sa.String(), nullable=True))
    op.add_column('call_attempts', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('call_attempts', sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('call_attempts', sa.Column('ai_agent_id', sa.UUID(), nullable=True))
    op.add_column('call_attempts', sa.Column('call_type', sa.String(), nullable=True))

    # Index for filtering by bolna_call_id (webhook lookups)
    op.create_index('ix_call_attempts_bolna_call_id', 'call_attempts', ['bolna_call_id'])


def downgrade() -> None:
    op.drop_index('ix_call_attempts_bolna_call_id', table_name='call_attempts')
    op.drop_column('call_attempts', 'call_type')
    op.drop_column('call_attempts', 'ai_agent_id')
    op.drop_column('call_attempts', 'ended_at')
    op.drop_column('call_attempts', 'started_at')
    op.drop_column('call_attempts', 'sentiment')
    op.drop_column('call_attempts', 'summary')
    op.drop_column('call_attempts', 'transcript')
    op.drop_column('call_attempts', 'call_status')
    op.drop_column('call_attempts', 'bolna_call_id')
