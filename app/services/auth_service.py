from __future__ import annotations

import logging
from gotrue.errors import AuthApiError
from app.db.supabase_client import get_supabase_admin_client
from app.core.exceptions import BadRequestError, UnauthorizedError

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self):
        self.supabase = get_supabase_admin_client()

    def login(self, email: str, password: str) -> dict:
        try:
            response = self.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password,
            })
            session = response.session
            if not session:
                raise UnauthorizedError("Invalid credentials")
            return {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "token_type": "bearer",
                "expires_in": session.expires_in,
                "user_id": str(response.user.id),
            }
        except AuthApiError as e:
            raise UnauthorizedError(str(e))

    def register(self, email: str, password: str, full_name: str, role: str = "telecaller", phone: str | None = None, vertical: str | None = None) -> dict:
        try:
            response = self.supabase.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "full_name": full_name,
                    "role": role,
                    "phone": phone,
                    "vertical": vertical,
                },
            })
            return {"user_id": str(response.user.id), "email": response.user.email}
        except AuthApiError as e:
            raise BadRequestError(str(e))

    def refresh_token(self, refresh_token: str) -> dict:
        try:
            response = self.supabase.auth._refresh_access_token(refresh_token)
            session = response.session
            if not session:
                raise UnauthorizedError("Invalid refresh token")
            return {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "token_type": "bearer",
                "expires_in": session.expires_in,
                "user_id": str(response.user.id),
            }
        except AuthApiError as e:
            raise UnauthorizedError(str(e))

    def reset_password(self, email: str) -> None:
        try:
            self.supabase.auth.reset_password_email(email)
        except AuthApiError as e:
            logger.warning("Password reset error: %s", e)

    def update_password(self, access_token: str, new_password: str) -> None:
        try:
            self.supabase.auth.admin.update_user_by_id(
                access_token,
                {"password": new_password},
            )
        except AuthApiError as e:
            raise BadRequestError(str(e))


def get_auth_service() -> AuthService:
    return AuthService()
