"""composite index for Kanban hot query

Revision ID: g2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-05-05

The Kanban board query filters on (company_id, current_stage, is_deleted)
and orders by created_at DESC. Existing indexes are single-column, so
Postgres has to bitmap-AND them and then sort. A covering composite
index lets the planner walk one B-tree, no sort. Especially helpful
on Admitverse with 19 active stages partitioning the data more finely.

CREATE INDEX CONCURRENTLY can't run inside a transaction, so we use
autocommit_block. The downside: if it fails midway you can be left with
an INVALID index that needs to be reindexed manually. Worth it because
the alternative blocks writes for the duration of the build.
"""
from alembic import op


revision = "g2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_kanban "
            "ON leads (company_id, current_stage, is_deleted, created_at DESC)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_leads_kanban")
