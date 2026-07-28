"""widen website_submissions.phone 20 → 32

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-28

`normalize_phone` returns its input unchanged when the value isn't a
clean 10- or 12-digit Indian number, and website phone fields are free
text — "98765 43210 call after 6pm" and similar are routine. At
String(20) any such value raised StringDataRightTruncation, which 500s
the ingest request and loses the lead.

32 covers realistic messy-but-real inputs ("+91 98765 43210 ext 2").
Anything still longer is parked in payload["phone_raw"] by the service
rather than truncated into a wrong number.

Widening a varchar is a metadata-only change in Postgres — no table
rewrite, no lock of consequence, safe on a live table.
"""
from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "website_submissions", "phone",
        existing_type=sa.String(20),
        type_=sa.String(32),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Narrowing would fail on any row already storing a longer value.
    op.execute("UPDATE website_submissions SET phone = left(phone, 20) WHERE length(phone) > 20")
    op.alter_column(
        "website_submissions", "phone",
        existing_type=sa.String(32),
        type_=sa.String(20),
        existing_nullable=True,
    )
