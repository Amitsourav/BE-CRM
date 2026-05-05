from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession
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
    # 1. Create the auth.users row in Supabase Auth.
    result = auth.register(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        phone=body.phone,
        vertical=body.vertical,
    )
    # 2. Create the matching profiles row tied to the admin's company.
    # Without this step, the user exists in auth but is invisible to the
    # CRM — they don't appear in dashboards, agent lists, or task
    # assignment dropdowns. FMC's legacy Supabase has a trigger that
    # auto-created profiles rows; the new Admitverse Supabase doesn't,
    # which is why this bug surfaced there first.
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
