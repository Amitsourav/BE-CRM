from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    phone: str | None = None
    role: str = "telecaller"
    vertical: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr


class UpdatePasswordRequest(BaseModel):
    access_token: str
    new_password: str


class MessageResponse(BaseModel):
    message: str
