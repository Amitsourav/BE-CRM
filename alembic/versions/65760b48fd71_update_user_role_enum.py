"""update_user_role_enum

Changes user_role ENUM from (admin, agent) to (admin, manager, telecaller).
Renames existing 'agent' values to 'telecaller', adds 'manager'.

Revision ID: 65760b48fd71
Revises: deb65076087d
Create Date: 2026-04-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '65760b48fd71'
down_revision: Union[str, None] = 'deb65076087d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL ENUM alteration requires:
    # 1. Add new values to the enum
    # 2. Rename existing data
    # 3. Cannot remove values from enum in PG, so we add new ones

    # Add 'manager' and 'telecaller' to the existing enum
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'manager'")
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'telecaller'")

    # Must commit enum changes before using new values in DML
    # (Alembic runs in a transaction, so we need to commit the type change)
    op.execute("COMMIT")

    # Rename all existing 'agent' records to 'telecaller'
    op.execute("UPDATE profiles SET role = 'telecaller' WHERE role = 'agent'")

    # Update default value
    op.execute("ALTER TABLE profiles ALTER COLUMN role SET DEFAULT 'telecaller'")


def downgrade() -> None:
    # Rename 'telecaller' back to 'agent'
    op.execute("UPDATE profiles SET role = 'agent' WHERE role = 'telecaller'")
    # Rename 'manager' to 'admin' (lossy but safe for downgrade)
    op.execute("UPDATE profiles SET role = 'admin' WHERE role = 'manager'")
    op.execute("ALTER TABLE profiles ALTER COLUMN role SET DEFAULT 'agent'")
    # Note: Cannot remove values from PostgreSQL ENUM type.
    # The 'manager' and 'telecaller' values will remain in the enum
    # but won't be used after downgrade.
