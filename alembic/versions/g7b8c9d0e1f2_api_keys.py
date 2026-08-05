"""api_keys — long-lived revocable credentials for machine clients

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-05

A key authenticates as an existing service-account profile
(`profile_id`) rather than carrying permissions of its own, so every
audit column that FKs to `profiles.id` keeps working unchanged and the
integration's writes are attributable.

Only the SHA-256 of the key is stored (`key_hash`, UNIQUE so auth is a
single indexed probe). The plaintext is returned once at creation.

Additive: new table only, no existing table touched, no enum altered.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "g7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(),
            server_default=sa.text("true"), nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by"], ["profiles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["profiles.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    # Auth path: every authenticated API-key request probes key_hash.
    # The UNIQUE constraint already backs this with an index; the two
    # below serve the admin list endpoint and cascade lookups.
    op.create_index("ix_api_keys_company_id", "api_keys", ["company_id"])
    op.create_index("ix_api_keys_profile_id", "api_keys", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_profile_id", table_name="api_keys")
    op.drop_index("ix_api_keys_company_id", table_name="api_keys")
    op.drop_table("api_keys")
