from __future__ import annotations

from fastapi import HTTPException, status


class NotFoundError(HTTPException):
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ForbiddenError(HTTPException):
    def __init__(self, detail: str = "Forbidden"):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


class BadRequestError(HTTPException):
    def __init__(self, detail: str = "Bad request"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class UnauthorizedError(HTTPException):
    def __init__(self, detail: str = "Not authenticated"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class ConflictError(HTTPException):
    def __init__(self, detail: str = "Conflict"):
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=detail)


class DuplicateLeadError(BadRequestError):
    """A lead with this phone or email already exists in the tenant.

    Carries the existing lead's id so a machine client can pivot
    straight to updating it instead of falling back to a substring
    search that may match several rows (or none, if the caller's role
    can't see the lead).

    `detail` stays the exact human string it has always been — the
    frontend renders `detail` directly, so changing its type or wording
    would regress the Add Lead form's error toast. The extra context is
    surfaced as sibling keys by `duplicate_lead_exception_handler`
    (app/core/exception_handlers.py).
    """

    def __init__(self, field: str, value: str, existing_id, existing_name: str | None = None):
        self.field = field
        self.value = value
        self.existing_lead_id = str(existing_id)
        self.existing_lead_name = existing_name
        super().__init__(
            detail=f"A lead with {field} {value} already exists ({existing_name})."
        )


class InvalidTransitionError(BadRequestError):
    def __init__(self, from_stage: str, to_stage: str):
        super().__init__(
            detail=f"Invalid stage transition from '{from_stage}' to '{to_stage}'"
        )
