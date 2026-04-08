from fastapi import Depends
from app.core.constants import UserRole
from app.core.exceptions import ForbiddenError
from app.models.profile import Profile


def require_admin(current_user: Profile = Depends()):
    """Dependency that ensures the current user is an admin."""
    if current_user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return current_user


def require_manager(current_user: Profile = Depends()):
    """Dependency that ensures the current user is admin or manager."""
    if current_user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise ForbiddenError("Manager access required")
    return current_user


def require_role(*roles: UserRole):
    """Factory for role-based dependency."""
    def checker(current_user: Profile = Depends()):
        if current_user.role not in roles:
            raise ForbiddenError(f"Requires one of: {', '.join(r.value for r in roles)}")
        return current_user
    return checker
