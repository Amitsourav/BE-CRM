"""add_company_id_to_all_tables

Adds company_id (FK -> companies.id) to all tenant-scoped tables.

Strategy for existing data:
  1. Add column as NULLABLE
  2. Create a default company and backfill existing rows
  3. Set column to NOT NULL
  4. Add foreign key constraint and index

Revision ID: deb65076087d
Revises: d5645a906d3c
Create Date: 2026-04-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'deb65076087d'
down_revision: Union[str, None] = 'd5645a906d3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All tables that need company_id
TABLES = [
    "profiles",
    "leads",
    "lead_sources",
    "lead_stage_logs",
    "call_attempts",
    "tasks",
    "notifications",
    "csv_imports",
    "activity_logs",
]


def upgrade() -> None:
    # Step 1: Create a default company for existing data
    op.execute(
        """
        INSERT INTO companies (id, name, slug, timezone, is_active)
        VALUES (
            gen_random_uuid(),
            'Default Company',
            'default',
            'UTC',
            true
        )
        ON CONFLICT (slug) DO NOTHING
        """
    )

    for table in TABLES:
        # Step 2: Add company_id as NULLABLE first
        op.add_column(table, sa.Column('company_id', sa.UUID(), nullable=True))

        # Step 3: Backfill existing rows with the default company
        op.execute(
            f"""
            UPDATE {table}
            SET company_id = (SELECT id FROM companies WHERE slug = 'default' LIMIT 1)
            WHERE company_id IS NULL
            """
        )

        # Step 4: Set NOT NULL
        op.alter_column(table, 'company_id', nullable=False)

        # Step 5: Add foreign key constraint
        op.create_foreign_key(
            f"fk_{table}_company_id",
            table,
            "companies",
            ["company_id"],
            ["id"],
            ondelete="CASCADE",
        )

        # Step 6: Add index for query performance
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_constraint(f"fk_{table}_company_id", table, type_="foreignkey")
        op.drop_column(table, 'company_id')

    # Optionally remove the default company
    op.execute("DELETE FROM companies WHERE slug = 'default'")
