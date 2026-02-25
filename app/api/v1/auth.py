from fastapi import APIRouter, Depends
from app.schemas.auth import (
    LoginRequest, RegisterRequest, TokenResponse,
    RefreshRequest, ResetPasswordRequest, UpdatePasswordRequest,
    MessageResponse,
)
from app.services.auth_service import AuthService, get_auth_service
from app.dependencies import get_current_user, get_current_admin
from app.models.profile import Profile

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)):
    return auth.login(body.email, body.password)


@router.post("/register", response_model=MessageResponse)
def register(
    body: RegisterRequest,
    admin: Profile = Depends(get_current_admin),
    auth: AuthService = Depends(get_auth_service),
):
    result = auth.register(
        email=body.email,
        password=body.password,
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
def reset_password(body: ResetPasswordRequest, auth: AuthService = Depends(get_auth_service)):
    auth.reset_password(body.email)
    return MessageResponse(message="If the email exists, a reset link has been sent")


@router.put("/update-password", response_model=MessageResponse)
def update_password(body: UpdatePasswordRequest, auth: AuthService = Depends(get_auth_service)):
    auth.update_password(body.access_token, body.new_password)
    return MessageResponse(message="Password updated successfully")
