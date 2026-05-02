import asyncio
import uuid
from logging.config import fileConfig

import asyncpg.connection as _asyncpg_conn
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context


# Patch asyncpg statement-name generator so prepared statements don't
# collide on Supabase's transaction-mode pgbouncer. See
# app/db/session.py for the full explanation.
def _unique_id(self, prefix):  # noqa: ARG001
    return f"__asyncpg_{prefix}_{uuid.uuid4().hex}__"


_asyncpg_conn.Connection._get_unique_id = _unique_id


# -- Load app settings & models ------------------------------------------------
from app.config import get_settings
from app.models.base import Base

# Import ALL models so Base.metadata knows every table
from app.models import (  # noqa: F401
    Company, Profile, LeadSource, Lead, LeadStageLog,
    CallAttempt, Task, Notification, CSVImport, ActivityLog, AIAgent,
)

settings = get_settings()

# -- Alembic Config object ----------------------------------------------------
config = context.config

# Set up Python logging from the .ini file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is what Alembic uses for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without connecting)."""
    url = settings.async_database_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async engine."""
    connectable = create_async_engine(
        settings.async_database_url,
        poolclass=pool.NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
