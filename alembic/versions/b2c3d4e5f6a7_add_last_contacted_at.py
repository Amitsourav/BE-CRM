"""add_last_contacted_at_to_leads

Revision ID: b2c3d4e5f6a7
Revises: 5a044326b9cf
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: str = '5a044326b9cf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('last_contacted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('leads', 'last_contacted_at')
