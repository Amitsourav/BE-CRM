from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_current_user
from app.core.tenant import get_current_company_id
from app.models.activity_log import ActivityLog
from app.models.profile import Profile

router = APIRouter(prefix="/activity-logs", tags=["Activity Logs"])


@router.get("")
async def list_activities(
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    entity_type: str | None = Query(None, description="Filter by entity type: lead, call, agent"),
    entity_id: uuid.UUID | None = Query(None, description="Filter by entity ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List activity logs for the company."""
    query = (
        select(ActivityLog)
        .where(ActivityLog.company_id == company_id)
        .order_by(ActivityLog.created_at.desc())
    )

    if entity_type:
        query = query.where(ActivityLog.entity_type == entity_type)
    if entity_id:
        query = query.where(ActivityLog.entity_id == entity_id)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    activities = result.scalars().all()

    return {
        "items": [
            {
                "id": str(a.id),
                "entity_type": a.entity_type,
                "entity_id": str(a.entity_id) if a.entity_id else None,
                "action": a.action,
                "old_values": a.old_values,
                "new_values": a.new_values,
                "actor_id": str(a.actor_id) if a.actor_id else None,
                "created_at": a.created_at,
            }
            for a in activities
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
