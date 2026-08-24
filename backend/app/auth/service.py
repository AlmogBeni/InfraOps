"""Authentication service.

Currently implements the local development authentication mode against the
users table. The interface (authenticate / issue tokens / refresh) is the
same surface an LDAP or OIDC provider would implement, so enterprise
authentication can be added without touching route handlers.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.security import create_access_token, create_refresh_token, decode_token, verify_password
from app.models.user import User

log = get_logger(__name__)


class AuthService:
    async def authenticate(self, db: AsyncSession, username: str, password: str) -> User:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active or not user.password_hash:
            # Uniform failure — do not reveal whether the account exists.
            raise AuthenticationError("Invalid username or password.")
        if not verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid username or password.")
        return user

    async def issue_tokens(self, user: User) -> tuple[str, dt.datetime, str]:
        roles = user.role_names
        access_token, expires_at = create_access_token(str(user.id), roles)
        refresh_token, _, _ = create_refresh_token(str(user.id))
        return access_token, expires_at, refresh_token

    def decode_access(self, token: str) -> dict[str, Any]:
        """Decode and validate an access token, returning its claims."""
        return decode_token(token, expected_type="access")

    async def user_from_refresh_token(self, db: AsyncSession, refresh_token: str) -> User:
        payload = decode_token(refresh_token, expected_type="refresh")
        user = await self.get_active_user(db, str(payload["sub"]))
        if user is None:
            raise AuthenticationError("Account no longer active.")
        return user

    async def get_active_user(self, db: AsyncSession, user_id: str) -> User | None:
        from uuid import UUID

        try:
            parsed = UUID(user_id)
        except ValueError:
            return None
        result = await db.execute(select(User).where(User.id == parsed))
        user = result.scalar_one_or_none()
        if user is not None and not user.is_active:
            return None
        return user


auth_service = AuthService()
