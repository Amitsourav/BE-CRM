"""add_campaigns_and_campaign_leads

Revision ID: 5a044326b9cf
Revises: a1b2c3d4e5f6
Create Date: 2026-04-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '5a044326b9cf'
down_revision: str = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('campaigns',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ai_agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column('start_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('daily_start_time', sa.Time(), server_default=sa.text("'09:00:00'"), nullable=False),
        sa.Column('daily_end_time', sa.Time(), server_default=sa.text("'19:00:00'"), nullable=False),
        sa.Column('skip_weekends', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('timezone', sa.String(length=50), server_default=sa.text("'Asia/Kolkata'"), nullable=False),
        sa.Column('max_retries', sa.Integer(), server_default=sa.text('3'), nullable=False),
        sa.Column('retry_gap_hours', sa.Integer(), server_default=sa.text('2'), nullable=False),
        sa.Column('max_concurrent_calls', sa.Integer(), server_default=sa.text('5'), nullable=False),
        sa.Column('total_leads', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('calls_made', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('calls_connected', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('calls_failed', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('total_cost_usd', sa.Float(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['ai_agent_id'], ['ai_agents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['profiles.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_campaigns_company_id', 'campaigns', ['company_id'])
    op.create_index('idx_campaigns_status', 'campaigns', ['status'])

    op.create_table('campaign_leads',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('lead_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('company_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('attempt_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_retry_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_call_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('last_call_status', sa.String(length=50), nullable=True),
        sa.Column('last_error', sa.String(length=500), nullable=True),
        sa.Column('priority', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['last_call_id'], ['call_attempts.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_campaign_leads_campaign_id', 'campaign_leads', ['campaign_id'])
    op.create_index('idx_campaign_leads_company_id', 'campaign_leads', ['company_id'])
    op.create_index('idx_campaign_leads_lead_id', 'campaign_leads', ['lead_id'])
    op.create_index('idx_campaign_leads_next_retry', 'campaign_leads', ['next_retry_at'])
    op.create_index('idx_campaign_leads_priority', 'campaign_leads', ['priority'])
    op.create_index('idx_campaign_leads_status', 'campaign_leads', ['status'])


def downgrade() -> None:
    op.drop_table('campaign_leads')
    op.drop_table('campaigns')
