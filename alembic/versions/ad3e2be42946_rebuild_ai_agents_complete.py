"""rebuild_ai_agents_complete

Revision ID: ad3e2be42946
Revises: fc37f1caeb23
Create Date: 2026-04-06 19:06:42.991239

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ad3e2be42946'
down_revision: Union[str, None] = 'fc37f1caeb23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to ai_agents
    op.add_column('ai_agents', sa.Column('created_by', sa.UUID(), nullable=True))
    op.add_column('ai_agents', sa.Column('role', sa.String(length=50), server_default=sa.text("'sales'"), nullable=False))
    op.add_column('ai_agents', sa.Column('welcome_message', sa.String(length=300), server_default=sa.text("'Hello! Am I speaking with {name}?'"), nullable=False))
    op.add_column('ai_agents', sa.Column('final_message_en', sa.String(length=300), server_default=sa.text("'Thank you for your time! Have a great day. Goodbye!'"), nullable=False))
    op.add_column('ai_agents', sa.Column('final_message_hi', sa.String(length=300), server_default=sa.text("'Bahut shukriya! Aapka din achha rahe. Alvida!'"), nullable=False))
    op.add_column('ai_agents', sa.Column('silence_message_en', sa.String(length=200), server_default=sa.text("'Hey, are you still there?'"), nullable=False))
    op.add_column('ai_agents', sa.Column('silence_message_hi', sa.String(length=200), server_default=sa.text("'Hello? Kya aap abhi bhi wahan hain?'"), nullable=False))
    op.add_column('ai_agents', sa.Column('llm_provider', sa.String(length=50), server_default=sa.text("'openrouter'"), nullable=False))
    op.add_column('ai_agents', sa.Column('llm_model', sa.String(length=100), server_default=sa.text("'openai/gpt-4o-mini'"), nullable=False))
    op.add_column('ai_agents', sa.Column('llm_temperature', sa.Float(), server_default=sa.text('0.8'), nullable=False))
    op.add_column('ai_agents', sa.Column('llm_max_tokens', sa.Integer(), server_default=sa.text('100'), nullable=False))
    op.add_column('ai_agents', sa.Column('stt_model', sa.String(length=100), server_default=sa.text("'saaras:v3'"), nullable=False))
    op.add_column('ai_agents', sa.Column('stt_keywords', sa.Text(), nullable=True))
    op.add_column('ai_agents', sa.Column('tts_provider', sa.String(length=50), server_default=sa.text("'sarvam'"), nullable=False))
    op.add_column('ai_agents', sa.Column('tts_model', sa.String(length=100), server_default=sa.text("'bulbul:v3'"), nullable=False))
    op.add_column('ai_agents', sa.Column('tts_voice', sa.String(length=100), server_default=sa.text("'simran'"), nullable=False))
    op.add_column('ai_agents', sa.Column('tts_gender', sa.String(length=10), server_default=sa.text("'female'"), nullable=False))
    op.add_column('ai_agents', sa.Column('tts_speed', sa.Float(), server_default=sa.text('1.0'), nullable=False))
    op.add_column('ai_agents', sa.Column('tts_buffer_size', sa.Integer(), server_default=sa.text('200'), nullable=False))
    op.add_column('ai_agents', sa.Column('tts_stability', sa.Float(), server_default=sa.text('0.5'), nullable=False))
    op.add_column('ai_agents', sa.Column('tts_similarity_boost', sa.Float(), server_default=sa.text('0.75'), nullable=False))
    op.add_column('ai_agents', sa.Column('primary_language', sa.String(length=10), server_default=sa.text("'en'"), nullable=False))
    op.add_column('ai_agents', sa.Column('secondary_language', sa.String(length=10), server_default=sa.text("'hi'"), nullable=False))
    op.add_column('ai_agents', sa.Column('auto_language_switch', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('ai_agents', sa.Column('language_style', sa.String(length=50), server_default=sa.text("'hinglish'"), nullable=False))
    op.add_column('ai_agents', sa.Column('endpointing_ms', sa.Integer(), server_default=sa.text('250'), nullable=False))
    op.add_column('ai_agents', sa.Column('linear_delay_ms', sa.Integer(), server_default=sa.text('400'), nullable=False))
    op.add_column('ai_agents', sa.Column('words_before_interrupt', sa.Integer(), server_default=sa.text('3'), nullable=False))
    op.add_column('ai_agents', sa.Column('max_response_words', sa.Integer(), server_default=sa.text('25'), nullable=False))
    op.add_column('ai_agents', sa.Column('precise_transcript', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('ai_agents', sa.Column('telephony_provider', sa.String(length=50), server_default=sa.text("'plivo'"), nullable=False))
    op.add_column('ai_agents', sa.Column('phone_number', sa.String(length=20), nullable=True))
    op.add_column('ai_agents', sa.Column('call_timeout_seconds', sa.Integer(), server_default=sa.text('600'), nullable=False))
    op.add_column('ai_agents', sa.Column('hangup_on_silence_seconds', sa.Integer(), server_default=sa.text('10'), nullable=False))
    op.add_column('ai_agents', sa.Column('call_start_time', sa.String(length=10), server_default=sa.text("'09:00'"), nullable=False))
    op.add_column('ai_agents', sa.Column('call_end_time', sa.String(length=10), server_default=sa.text("'19:00'"), nullable=False))
    op.add_column('ai_agents', sa.Column('restrict_call_hours', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('ai_agents', sa.Column('voicemail_detection', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('ai_agents', sa.Column('noise_cancellation', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('ai_agents', sa.Column('noise_cancellation_level', sa.Integer(), server_default=sa.text('60'), nullable=False))
    op.add_column('ai_agents', sa.Column('ambient_noise', sa.String(length=50), server_default=sa.text("'office-ambience'"), nullable=False))
    op.add_column('ai_agents', sa.Column('silence_detection_seconds', sa.Integer(), server_default=sa.text('9'), nullable=False))
    op.add_column('ai_agents', sa.Column('webhook_url', sa.String(length=500), nullable=True))
    op.add_column('ai_agents', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_ai_agents_created_by', 'ai_agents', 'profiles', ['created_by'], ['id'], ondelete='SET NULL')

    # Drop old columns replaced by new ones
    op.drop_column('ai_agents', 'language')
    op.drop_column('ai_agents', 'voice_provider')
    op.drop_column('ai_agents', 'voice_id')
    op.drop_column('ai_agents', 'model_name')
    op.drop_column('ai_agents', 'language_secondary')


def downgrade() -> None:
    # Re-add old columns
    op.add_column('ai_agents', sa.Column('language_secondary', sa.VARCHAR(length=50), server_default=sa.text("'hi'"), nullable=True))
    op.add_column('ai_agents', sa.Column('model_name', sa.VARCHAR(length=50), server_default=sa.text("'gpt-4'"), nullable=False))
    op.add_column('ai_agents', sa.Column('voice_id', sa.VARCHAR(length=100), nullable=True))
    op.add_column('ai_agents', sa.Column('voice_provider', sa.VARCHAR(length=50), server_default=sa.text("'sarvam'"), nullable=False))
    op.add_column('ai_agents', sa.Column('language', sa.VARCHAR(length=50), server_default=sa.text("'en'"), nullable=False))

    # Drop foreign key and new columns
    op.drop_constraint('fk_ai_agents_created_by', 'ai_agents', type_='foreignkey')
    op.drop_column('ai_agents', 'deleted_at')
    op.drop_column('ai_agents', 'webhook_url')
    op.drop_column('ai_agents', 'silence_detection_seconds')
    op.drop_column('ai_agents', 'ambient_noise')
    op.drop_column('ai_agents', 'noise_cancellation_level')
    op.drop_column('ai_agents', 'noise_cancellation')
    op.drop_column('ai_agents', 'voicemail_detection')
    op.drop_column('ai_agents', 'restrict_call_hours')
    op.drop_column('ai_agents', 'call_end_time')
    op.drop_column('ai_agents', 'call_start_time')
    op.drop_column('ai_agents', 'hangup_on_silence_seconds')
    op.drop_column('ai_agents', 'call_timeout_seconds')
    op.drop_column('ai_agents', 'phone_number')
    op.drop_column('ai_agents', 'telephony_provider')
    op.drop_column('ai_agents', 'precise_transcript')
    op.drop_column('ai_agents', 'max_response_words')
    op.drop_column('ai_agents', 'words_before_interrupt')
    op.drop_column('ai_agents', 'linear_delay_ms')
    op.drop_column('ai_agents', 'endpointing_ms')
    op.drop_column('ai_agents', 'language_style')
    op.drop_column('ai_agents', 'auto_language_switch')
    op.drop_column('ai_agents', 'secondary_language')
    op.drop_column('ai_agents', 'primary_language')
    op.drop_column('ai_agents', 'tts_similarity_boost')
    op.drop_column('ai_agents', 'tts_stability')
    op.drop_column('ai_agents', 'tts_buffer_size')
    op.drop_column('ai_agents', 'tts_speed')
    op.drop_column('ai_agents', 'tts_gender')
    op.drop_column('ai_agents', 'tts_voice')
    op.drop_column('ai_agents', 'tts_model')
    op.drop_column('ai_agents', 'tts_provider')
    op.drop_column('ai_agents', 'stt_keywords')
    op.drop_column('ai_agents', 'stt_model')
    op.drop_column('ai_agents', 'llm_max_tokens')
    op.drop_column('ai_agents', 'llm_temperature')
    op.drop_column('ai_agents', 'llm_model')
    op.drop_column('ai_agents', 'llm_provider')
    op.drop_column('ai_agents', 'silence_message_hi')
    op.drop_column('ai_agents', 'silence_message_en')
    op.drop_column('ai_agents', 'final_message_hi')
    op.drop_column('ai_agents', 'final_message_en')
    op.drop_column('ai_agents', 'welcome_message')
    op.drop_column('ai_agents', 'role')
    op.drop_column('ai_agents', 'created_by')
