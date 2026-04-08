"""add_phone_number_to_calls

Revision ID: 3ab1c0de4f01
Revises: 2dc2e3bfb2a4
Create Date: 2026-04-07 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3ab1c0de4f01'
down_revision: Union[str, None] = '2dc2e3bfb2a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'call_attempts',
        sa.Column('phone_number', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('call_attempts', 'phone_number')
