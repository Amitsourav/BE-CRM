import math
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def paginate(db: AsyncSession, query: Select, page: int = 1, page_size: int = 25):
    page = max(1, page)
    page_size = min(max(1, page_size), 100)

    # Count total — strip ORDER BY before wrapping as a subquery.
    # Postgres doesn't need to sort rows it's only counting, and keeping the
    # ORDER BY forces the planner to either keep it or prove it's safe to drop.
    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Fetch page
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    items = result.scalars().all()

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total > 0 else 0,
    }
