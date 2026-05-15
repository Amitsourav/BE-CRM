"""Bootstrap a fresh Supabase project from current SQLAlchemy models.

The historical alembic baseline (d2bd1aba9cb6) is empty — it documents
that the original tables (profiles, leads, etc.) were created by hand
in Supabase before alembic was wired in. That assumption breaks for any
new Supabase project where no manual setup happened, so we have to
recreate the schema in code.

Two PostgreSQL-specific gotchas this module handles:

1. Several model columns use `ENUM(..., create_type=False)`, which means
   SQLAlchemy will NOT auto-create the type on `create_all`. We CREATE
   TYPE up-front in raw SQL so the subsequent CREATE TABLE statements
   resolve their column types.

2. `gen_random_uuid()` needs the pgcrypto extension. Supabase enables
   it by default, but a `CREATE EXTENSION IF NOT EXISTS` is cheap
   insurance.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


# All Postgres ENUM types referenced by models with `create_type=False`.
# Order doesn't matter (no inter-enum dependencies).
ENUM_TYPES: dict[str, list[str]] = {
    "user_role": ["admin", "manager", "pre_counsellor"],
    "task_type": ["follow_up", "call", "meeting", "document_collection", "application", "other"],
    "task_status": ["pending", "in_progress", "completed", "overdue"],
    "lead_source_type": ["csv", "meta_ads", "manual", "whatsapp"],
    "notification_type": [
        "lead_assigned", "task_created", "task_overdue",
        "dnp_warning", "dnp_auto_lost", "stage_changed",
        "csv_import_complete", "general",
    ],
    "lead_stage": ["lead", "called", "connected", "qualified_lead", "won", "lost"],
    "csv_import_status": ["uploaded", "previewing", "processing", "completed", "failed"],
    "call_disposition": ["dnp", "connected", "busy", "switched_off", "wrong_number", "callback"],
}


async def is_fresh_db(engine: AsyncEngine) -> bool:
    """True iff the public schema has no `alembic_version` table."""
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = 'alembic_version'"
            ")"
        ))
        return not bool(result.scalar())


async def bootstrap_schema(engine: AsyncEngine) -> None:
    """Create extensions, ENUM types, and all tables on a fresh DB."""
    # Import here so model registration with Base.metadata happens before
    # we ask metadata to emit DDL. Importing the package triggers all
    # individual model imports via __init__.py.
    from app.models import Base  # noqa: F401
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        # 1. Extensions — pgcrypto for gen_random_uuid() (Supabase has it,
        # but be explicit so this also works on stock Postgres).
        logger.info("BOOTSTRAP: ensuring pgcrypto extension")
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

        # 2. ENUM types — CREATE TYPE has no IF NOT EXISTS, so wrap in DO/EXCEPTION.
        for enum_name, values in ENUM_TYPES.items():
            quoted = ", ".join(f"'{v}'" for v in values)
            logger.info("BOOTSTRAP: ensuring enum type %s", enum_name)
            await conn.execute(text(
                f"DO $$ BEGIN "
                f"  CREATE TYPE {enum_name} AS ENUM ({quoted}); "
                f"EXCEPTION WHEN duplicate_object THEN NULL; "
                f"END $$;"
            ))

        # 3. Tables — Base.metadata.create_all is idempotent (checkfirst=True).
        logger.info("BOOTSTRAP: creating tables from SQLAlchemy metadata")
        await conn.run_sync(app.models.Base.metadata.create_all)

    logger.info("BOOTSTRAP: schema creation complete")
