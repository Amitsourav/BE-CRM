"""update_ai_agents_for_sarvam

Adds stt_provider and language_secondary columns.
Updates voice_provider default from 'elevenlabs' to 'sarvam'.
Updates existing rows to use sarvam defaults.

Revision ID: fc37f1caeb23
Revises: 2aac85e3cee8
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'fc37f1caeb23'
down_revision: Union[str, None] = '2aac85e3cee8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns
    op.add_column('ai_agents', sa.Column(
        'stt_provider', sa.String(50),
        server_default=sa.text("'sarvam'"), nullable=False,
    ))
    op.add_column('ai_agents', sa.Column(
        'language_secondary', sa.String(50),
        server_default=sa.text("'hi'"), nullable=True,
    ))

    # Update voice_provider default from 'elevenlabs' to 'sarvam'
    op.alter_column('ai_agents', 'voice_provider',
                    server_default=sa.text("'sarvam'"))

    # Update existing rows that had 'elevenlabs' to 'sarvam'
    op.execute("UPDATE ai_agents SET voice_provider = 'sarvam' WHERE voice_provider = 'elevenlabs'")


def downgrade() -> None:
    # Revert existing rows
    op.execute("UPDATE ai_agents SET voice_provider = 'elevenlabs' WHERE voice_provider = 'sarvam'")

    # Revert voice_provider default
    op.alter_column('ai_agents', 'voice_provider',
                    server_default=sa.text("'elevenlabs'"))

    # Drop new columns
    op.drop_column('ai_agents', 'language_secondary')
    op.drop_column('ai_agents', 'stt_provider')
