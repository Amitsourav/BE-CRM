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
#
# The seven that have a matching enum class in constants.py are DERIVED
# from those classes rather than copied. That is not tidiness — a
# hand-maintained copy silently rots, and the failure is invisible until
# a brand-new deployment tries to write its first row.
#
# It had already rotted. This dict used to hard-code `lead_stage` as the
# original six values (lead/called/connected/qualified_lead/won/lost)
# while `LeadStage` had grown to 29 across the FMC and Admitverse
# pipelines, and it was missing four types outright. On a fresh database
# that combination fails twice over: `create_all` cannot build
# lead_banks / lead_applications / leads without the four missing types,
# and even past that, `alembic stamp head` marks every value-adding
# migration as already applied — so the 23 absent stage values never
# arrive and the very first lead insert (`current_stage='created'`)
# fails. Admitverse escaped this in May 2026 only because the list
# happened to be current on the day it was bootstrapped; every migration
# after that reached it normally through `upgrade head`.
#
# Deriving them means a value added to an enum class in constants.py is
# automatically correct here, for every future deployment.
#
# Order doesn't matter (no inter-enum dependencies).


def _enum_types() -> dict[str, list[str]]:
    """Build the type→values map from the models' own source of truth.

    Imported inside the function, not at module scope, to keep this
    module free of import-order coupling with app.core.constants.
    """
    from app.core import constants as c

    def vals(enum_cls) -> list[str]:
        return [m.value for m in enum_cls]

    return {
        # Derived — these have a canonical enum class.
        "user_role": vals(c.UserRole),
        "task_type": vals(c.TaskType),
        "task_status": vals(c.TaskStatus),
        "lead_source_type": vals(c.LeadSourceType),
        "notification_type": vals(c.NotificationType),
        "lead_stage": vals(c.LeadStage),
        "csv_import_status": vals(c.CSVImportStatus),
        "call_disposition": vals(c.CallDisposition),
        # Declared inline on their models, with no class in constants.py,
        # so they must be listed. Keep in step with:
        #   bank_status        → models/lead_bank.py + models/lead.py
        #                        (ONE db type shared by both columns)
        #   pf_status_enum     → models/lead_bank.py
        #   application_status → models/lead_application.py
        #   visa_status_enum   → models/lead_application.py
        "bank_status": [
            "applied", "docs_reviewed", "under_review", "loan_login",
            "sanctioned", "pf_paid", "disbursed", "lost",
        ],
        "pf_status_enum": ["paid", "pending"],
        "application_status": [
            "applied", "shortlisted", "offer_received", "conditional_offer",
            "unconditional_offer", "deposit_paid", "cas_received",
            "visa_applied", "visa_approved", "enrolled", "rejected",
            "withdrawn",
        ],
        "visa_status_enum": ["not_started", "applied", "approved", "rejected"],
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
        for enum_name, values in _enum_types().items():
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
