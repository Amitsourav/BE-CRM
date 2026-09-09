"""Iconiq Energy brand: 3 pipeline stages + 8 lead capture fields

Revision ID: x5y6z7a8b9c0
Revises: w3x4y5z6a7b8
Create Date: 2026-09-09

Iconiq Energy is the third brand on this codebase (slug `iconiq`) — a
manufacturer of hybrid inverters, Li-ion batteries and BESS. Its CRM is
leads / calls / pipeline / tasks / notifications only: no AI voice agent,
no loan or commission tracking, no admissions.

Two additive changes:

1. Three new `lead_stage` values. Iconiq's board is
   created -> contacted -> not_interested | interested -> quoted ->
   won | lost. `created`, `contacted`, `lost` and `won` already exist
   ('won' is one of the six original values and no other brand uses it),
   so only three are new. ADD VALUE cannot run inside a transaction,
   hence the autocommit block; IF NOT EXISTS makes a re-run a no-op
   (PG 12+). Same pattern as e5f6a7b8c9d0.

2. Eight nullable columns on `leads` for Iconiq's capture form. Real
   columns rather than `custom_fields` JSONB because CSV import, list
   filters and search only see columns.

Nothing existing is altered and every column is nullable, so this is
safe on the live FMC and Admitverse databases: they gain three enum
values they never select and eight columns that stay NULL — exactly as
Iconiq will never populate the loan or university tiles.
"""
from alembic import op
import sqlalchemy as sa


revision = "x5y6z7a8b9c0"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


_NEW_STAGES = ("not_interested", "interested", "quoted")

_NEW_COLUMNS = (
    ("organization", sa.String(200)),
    ("website", sa.String(255)),
    ("application_industry", sa.String(100)),
    ("load_capacity_kw", sa.Numeric(10, 2)),
    ("backup_duration_hours", sa.Numeric(6, 2)),
    ("solar_present", sa.Boolean()),
    ("solar_capacity_kw", sa.Numeric(10, 2)),
    ("dg_available", sa.Boolean()),
    ("dg_capacity_kva", sa.Numeric(10, 2)),
)


def upgrade() -> None:
    # 1. Stage values — must run outside a transaction.
    with op.get_context().autocommit_block():
        for value in _NEW_STAGES:
            op.execute(f"ALTER TYPE lead_stage ADD VALUE IF NOT EXISTS '{value}'")

    # 2. Capture fields. All nullable, no defaults, no backfill.
    for name, type_ in _NEW_COLUMNS:
        op.add_column("leads", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    # Only the columns come back off. Postgres cannot DROP a value from
    # an enum type, and rebuilding `lead_stage` would mean rewriting
    # every column that uses it (leads.current_stage,
    # lead_stage_logs.from_stage / to_stage) on a live table. Unused
    # values are harmless, so they stay.
    for name, _ in reversed(_NEW_COLUMNS):
        op.drop_column("leads", name)
