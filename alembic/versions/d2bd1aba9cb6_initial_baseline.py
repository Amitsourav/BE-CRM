"""initial_baseline

Stamps the database as having the initial schema.
All existing tables (profiles, leads, lead_sources, lead_stage_logs,
call_attempts, tasks, notifications, csv_imports, activity_logs)
were created directly in Supabase before Alembic was set up.

This is an empty migration that serves as the baseline.
After running `alembic stamp head` on the existing database,
all future schema changes will be tracked via Alembic.

Revision ID: d2bd1aba9cb6
Revises:
Create Date: 2026-04-01 19:09:50.594465

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2bd1aba9cb6'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline — existing tables already created in Supabase.
    # No operations needed.
    pass


def downgrade() -> None:
    # Cannot reverse the baseline.
    pass
