"""add_dual_tts_fields

Revision ID: 2dc2e3bfb2a4
Revises: ad3e2be42946
Create Date: 2026-04-07 16:52:37.726062

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2dc2e3bfb2a4'
down_revision: Union[str, None] = 'ad3e2be42946'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('ai_agents', sa.Column('tts_provider_english', sa.String(length=50), nullable=True))
    op.add_column('ai_agents', sa.Column('tts_model_english', sa.String(length=100), nullable=True))
    op.add_column('ai_agents', sa.Column('tts_voice_english', sa.String(length=100), nullable=True))
    op.add_column('ai_agents', sa.Column('tts_provider_hindi', sa.String(length=50), nullable=True))
    op.add_column('ai_agents', sa.Column('tts_model_hindi', sa.String(length=100), nullable=True))
    op.add_column('ai_agents', sa.Column('tts_voice_hindi', sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column('ai_agents', 'tts_voice_hindi')
    op.drop_column('ai_agents', 'tts_model_hindi')
    op.drop_column('ai_agents', 'tts_provider_hindi')
    op.drop_column('ai_agents', 'tts_voice_english')
    op.drop_column('ai_agents', 'tts_model_english')
    op.drop_column('ai_agents', 'tts_provider_english')
