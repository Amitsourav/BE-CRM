import uuid

import asyncpg.connection as _asyncpg_conn
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import get_settings

settings = get_settings()


# Supabase's transaction-mode pgbouncer reuses the same backend Postgres
# connection across many client transactions. asyncpg, by default, names
# its prepared statements `__asyncpg_stmt_<N>__` with a process-global
# counter, which collides on reused backends across processes:
#
#     DuplicatePreparedStatementError: prepared statement
#     "__asyncpg_stmt_3__" already exists
#
# asyncpg has no public API for naming prepared statements, so we patch
# Connection._get_unique_id to suffix each name with a UUID — collisions
# are then mathematically impossible. statement_cache_size=0 still helps
# because we don't want asyncpg trying to *reuse* cached PS handles that
# pgbouncer may have rotated out from under us.
def _unique_id(self, prefix):  # noqa: ARG001
    return f"__asyncpg_{prefix}_{uuid.uuid4().hex}__"


_asyncpg_conn.Connection._get_unique_id = _unique_id


engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "command_timeout": 60,
    },
    pool_recycle=300,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
