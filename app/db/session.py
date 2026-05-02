import uuid

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import get_settings

settings = get_settings()


# Supabase's transaction-mode pgbouncer reuses the same backend Postgres
# connection across many client transactions. asyncpg, by default, names
# its prepared statements `__asyncpg_stmt_<N>__` with a per-client-conn
# counter, which collides on reused backends:
#
#     DuplicatePreparedStatementError: prepared statement
#     "__asyncpg_stmt_3__" already exists
#
# Fix: give every prepared statement a UUID-suffixed name so collisions
# are impossible. Plus statement_cache_size=0 to avoid asyncpg trying to
# reuse cached PS handles that pgbouncer has already invalidated.
def _unique_stmt_name() -> str:
    return f"__asyncpg_{uuid.uuid4().hex}__"


engine = create_async_engine(
    settings.async_database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": _unique_stmt_name,
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
