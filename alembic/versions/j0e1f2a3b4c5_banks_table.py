"""banks — canonical lender list, editable without a deploy

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-08-08

The lender list was a Python tuple, so every new lending relationship
needed a code change and a release before shares from that lender's
WhatsApp group could be recorded — and until then the whole relationship
was invisible in the CRM (no grid column, every share 400ing). Two
lenders were requested in one week with nine more groups already joined.

Still a CONTROLLED vocabulary: adding a name is admin-only. The list was
locked because free-typing produced sbi/SBI, Unicred/UniCred and
Poonawala/Poonawalla in the same database. What changes is that adding to
it no longer requires a deploy.

Seeded from FMC_BANKS so behaviour is identical the moment this runs.
That constant stays as the seed and as the fallback for an empty table.

The case-insensitive unique index is the point: it makes "gyandhan" and
"GyanDhan" the same entry, so the drift can't come back through the new
endpoint.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "j0e1f2a3b4c5"
down_revision = "i9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "banks",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_banks_name"),
        sa.ForeignKeyConstraint(["created_by"], ["profiles.id"], ondelete="SET NULL"),
    )
    # Stops "gyandhan" being added next to "GyanDhan" — the whole reason
    # this vocabulary is controlled rather than free text.
    op.execute("CREATE UNIQUE INDEX uq_banks_name_lower ON banks (lower(name))")

    # Seed in the constant's order so the dropdown and the grid columns
    # look exactly as they did before this ran.
    from app.core.constants import FMC_BANKS
    conn = op.get_bind()
    for i, name in enumerate(FMC_BANKS):
        conn.execute(
            sa.text(
                "INSERT INTO banks (name, sort_order) VALUES (:n, :o) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"n": name, "o": i},
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_banks_name_lower")
    op.drop_table("banks")
