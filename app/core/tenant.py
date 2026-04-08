from __future__ import annotations

import uuid
from fastapi import Depends
from app.dependencies import get_current_user
from app.models.profile import Profile
from app.core.exceptions import ForbiddenError


async def get_current_company_id(
    current_user: Profile = Depends(get_current_user),
) -> uuid.UUID:
    """Extract company_id from the logged-in user's profile.

    Every tenant-scoped query should use this to filter data.
    Ensures no user can ever access another company's data.
    """
    if not current_user.company_id:
        raise ForbiddenError("User is not associated with a company")
    return current_user.company_id
