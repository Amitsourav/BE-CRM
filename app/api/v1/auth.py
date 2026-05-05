import logging
from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
from app.schemas.auth import (
    LoginRequest, RegisterRequest, TokenResponse,
    RefreshRequest, ResetPasswordRequest, UpdatePasswordRequest,
    MessageResponse,
)
from app.services.auth_service import AuthService, get_auth_service
from app.dependencies import get_current_user, get_current_admin
from app.db.session import get_db
from app.models.profile import Profile

router = APIRouter(prefix="/auth", tags=["Auth"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def login(request: Request, body: LoginRequest, auth: AuthService = Depends(get_auth_service)):
    return auth.login(body.email, body.password)


@router.post("/register", response_model=MessageResponse)
@limiter.limit("3/minute")
async def register(
    request: Request,
    body: RegisterRequest,
    admin: Profile = Depends(get_current_admin),
    auth: AuthService = Depends(get_auth_service),
    db: AsyncSession = Depends(get_db),
):
    logger.info(
        "REGISTER_START email=%s role=%s admin=%s company_id=%s",
        body.email, body.role, admin.email, admin.company_id,
    )
    # 1. Create the auth.users row in Supabase Auth.
    result = auth.register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        phone=body.phone,
        vertical=body.vertical,
    )
    logger.info(
        "REGISTER_AUTH_USER_CREATED user_id=%s email=%s",
        result["user_id"], result["email"],
    )
    # 2. Create the matching profiles row tied to the admin's company.
    # Belt and suspenders: even if the new handle_new_user() trigger
    # already inserted a default row, ON CONFLICT (id) DO UPDATE
    # promotes it with the admin-supplied role / company / fields.
    try:
        await auth.create_profile_row(
            db,
            user_id=result["user_id"],
            company_id=admin.company_id,
            email=result["email"],
            full_name=body.full_name,
            role=body.role,
            phone=body.phone,
            vertical=body.vertical,
        )
        logger.info(
            "REGISTER_PROFILE_CREATED user_id=%s company_id=%s",
            result["user_id"], admin.company_id,
        )
    except Exception:
        # Don't leave the call as a 500 if the trigger already created
        # the row — log loudly so we can find the root cause but let
        # the user appear in the UI (the trigger's row is correct in
        # single-tenant Supabase).
        logger.exception(
            "REGISTER_PROFILE_FAILED user_id=%s — relying on auth.users trigger",
            result["user_id"],
        )
    return MessageResponse(message=f"User created: {result['email']}")


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, auth: AuthService = Depends(get_auth_service)):
    return auth.refresh_token(body.refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(current_user: Profile = Depends(get_current_user)):
    return MessageResponse(message="Logged out successfully")


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("3/minute")
def reset_password(request: Request, body: ResetPasswordRequest, auth: AuthService = Depends(get_auth_service)):
    auth.reset_password(body.email)
    return MessageResponse(message="If the email exists, a reset link has been sent")


@router.put("/update-password", response_model=MessageResponse)
def update_password(body: UpdatePasswordRequest, auth: AuthService = Depends(get_auth_service)):
    auth.update_password(body.access_token, body.new_password)
    return MessageResponse(message="Password updated successfully")
