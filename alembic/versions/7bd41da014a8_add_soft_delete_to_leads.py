"""add_soft_delete_to_leads

Adds is_deleted (boolean) and deleted_at (timestamp) to leads table
for soft delete support.

Revision ID: 7bd41da014a8
Revises: 65760b48fd71
Create Date: 2026-04-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7bd41da014a8'
down_revision: Union[str, None] = '65760b48fd71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('leads', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_leads_is_deleted', 'leads', ['is_deleted'])


def downgrade() -> None:
    op.drop_index('ix_leads_is_deleted', table_name='leads')
    op.drop_column('leads', 'deleted_at')
    op.drop_column('leads', 'is_deleted')
