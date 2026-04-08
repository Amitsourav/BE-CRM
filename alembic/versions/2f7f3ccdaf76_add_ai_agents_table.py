"""add_ai_agents_table

Revision ID: 2f7f3ccdaf76
Revises: 34a3d5d2994b
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '2f7f3ccdaf76'
down_revision: Union[str, None] = '34a3d5d2994b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_agents',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('company_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('model_name', sa.String(50), server_default=sa.text("'gpt-4'"), nullable=False),
        sa.Column('voice_provider', sa.String(50), server_default=sa.text("'elevenlabs'"), nullable=False),
        sa.Column('voice_id', sa.String(100), nullable=True),
        sa.Column('language', sa.String(50), server_default=sa.text("'en'"), nullable=False),
        sa.Column('tone', sa.String(50), server_default=sa.text("'friendly'"), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_ai_agents_company_id', 'ai_agents', ['company_id'])
    op.create_index('ix_ai_agents_is_active', 'ai_agents', ['is_active'])
    op.create_index('ix_ai_agents_is_default', 'ai_agents', ['company_id', 'is_default'])


def downgrade() -> None:
    op.drop_index('ix_ai_agents_is_default', table_name='ai_agents')
    op.drop_index('ix_ai_agents_is_active', table_name='ai_agents')
    op.drop_index('ix_ai_agents_company_id', table_name='ai_agents')
    op.drop_table('ai_agents')
