from __future__ import annotations

import logging
import uuid
from gotrue.errors import AuthApiError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
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

    def register(self, email: str, password: str, full_name: str, role: str = "pre_counsellor", phone: str | None = None, vertical: str | None = None) -> dict:
        """Create the auth.users row only.

        Callers should follow up with `create_profile_row` so the user is
        actually visible to the CRM. Splitting these two steps lets the
        endpoint pass through the admin's company_id (which lives in the
        request session, not in this service).
        """
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

    async def create_profile_row(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        company_id: uuid.UUID,
        email: str,
        full_name: str,
        role: str = "pre_counsellor",
        phone: str | None = None,
        vertical: str | None = None,
    ) -> None:
        """Insert (or upsert) the corresponding profiles row.

        The CRM looks up everything tenant-scoped through `profiles`
        (company_id, role, role-based agent lists, task assignment, dashboards).
        Creating only auth.users — which is what the previous register did —
        leaves a phantom user that doesn't show up anywhere. FMC's old
        Supabase masked the bug via a `handle_new_user()` trigger that
        auto-created a profiles row; the fresh Admitverse Supabase has no
        such trigger, exposing the issue.

        ON CONFLICT keeps the operation idempotent: if a trigger somewhere
        already inserted a default row, we promote it to the right
        company/role rather than failing.
        """
        await db.execute(
            text(
                "INSERT INTO profiles "
                "(id, company_id, email, full_name, role, phone, vertical, is_active) "
                "VALUES (:id, :cid, :email, :name, :role, :phone, :vertical, true) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  company_id = EXCLUDED.company_id, "
                "  email = EXCLUDED.email, "
                "  full_name = COALESCE(NULLIF(EXCLUDED.full_name, ''), profiles.full_name), "
                "  role = EXCLUDED.role, "
                "  phone = EXCLUDED.phone, "
                "  vertical = EXCLUDED.vertical, "
                "  is_active = true"
            ),
            {
                "id": user_id,
                "cid": company_id,
                "email": email,
                "name": full_name,
                "role": role,
                "phone": phone,
                "vertical": vertical,
            },
        )
        await db.commit()

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
