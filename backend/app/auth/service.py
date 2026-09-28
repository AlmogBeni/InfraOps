"""Authentication service.

Currently implements the local authentication mode against the users table.
The interface (authenticate / issue tokens / refresh) is the same surface an
LDAP or OIDC provider would implement, so enterprise authentication can be
added without touching route handlers.

Refresh tokens are registered server-side (``refresh_tokens``). Each refresh
rotates the token; presenting an already-rotated or revoked token revokes the
whole family descended from that login (token theft detection). Logout
revokes the family.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.auth_tokens import RefreshToken
from app.models.user import User

log = get_logger(__name__)

# Two browser tabs may legitimately refresh with the same cookie at the same
# moment. A second use within this window is treated as that race, not theft.
_CONCURRENT_REFRESH_GRACE = dt.timedelta(seconds=10)


@lru_cache
def _dummy_password_hash() -> str:
    """A real scrypt hash used to equalise timing for unknown usernames."""
    return hash_password(uuid.uuid4().hex)


class RefreshTokenReuseError(AuthenticationError):
    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__("Session was revoked. Please sign in again.")
        self.user_id = user_id


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_at: dt.datetime
    refresh_token: str


class AuthService:
    async def authenticate(self, db: AsyncSession, username: str, password: str) -> User:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if user is None or not user.is_active or not user.password_hash:
            # Uniform failure *and* uniform timing: always pay the scrypt cost
            # so response times do not reveal whether the account exists.
            verify_password(password, _dummy_password_hash())
            raise AuthenticationError("Invalid username or password.")
        if not verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid username or password.")
        return user

    async def issue_tokens(
        self, db: AsyncSession, user: User, *, family_id: str | None = None
    ) -> IssuedTokens:
        access_token, expires_at = create_access_token(str(user.id), user.role_names)
        refresh_token, jti, refresh_expires_at = create_refresh_token(str(user.id))
        db.add(
            RefreshToken(
                jti=jti,
                family_id=family_id or uuid.uuid4().hex,
                user_id=user.id,
                expires_at=refresh_expires_at,
            )
        )
        await db.flush()
        return IssuedTokens(access_token, expires_at, refresh_token)

    def decode_access(self, token: str) -> dict[str, Any]:
        """Decode and validate an access token, returning its claims."""
        return decode_token(token, expected_type="access")

    async def rotate_refresh_token(self, db: AsyncSession, refresh_token: str) -> tuple[User, IssuedTokens]:
        payload = decode_token(refresh_token, expected_type="refresh")
        jti = str(payload.get("jti") or "")
        row = (
            await db.execute(select(RefreshToken).where(RefreshToken.jti == jti).with_for_update())
        ).scalar_one_or_none()
        if row is None or str(row.user_id) != str(payload.get("sub")):
            raise AuthenticationError("Session is no longer valid. Please sign in again.")
        now = dt.datetime.now(dt.UTC)
        if row.revoked_at is not None:
            raise AuthenticationError("Session was signed out. Please sign in again.")
        if row.used_at is not None and now - row.used_at > _CONCURRENT_REFRESH_GRACE:
            await self.revoke_family(db, row.family_id)
            log.warning("Refresh token reuse detected for user %s; family revoked.", row.user_id)
            raise RefreshTokenReuseError(row.user_id)

        user = await self.get_active_user(db, str(row.user_id))
        if user is None:
            await self.revoke_family(db, row.family_id)
            raise AuthenticationError("Account no longer active.")
        if row.used_at is None:
            row.used_at = now
        tokens = await self.issue_tokens(db, user, family_id=row.family_id)
        return user, tokens

    async def revoke_family(self, db: AsyncSession, family_id: str) -> None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=dt.datetime.now(dt.UTC))
        )

    async def revoke_refresh_token(self, db: AsyncSession, refresh_token: str) -> uuid.UUID | None:
        """Revoke the family of a presented refresh token (logout). Never raises."""
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except AuthenticationError:
            return None
        row = await db.get(RefreshToken, str(payload.get("jti") or ""))
        if row is None:
            return None
        await self.revoke_family(db, row.family_id)
        return row.user_id

    async def purge_expired_refresh_tokens(self, db: AsyncSession) -> None:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        await db.execute(delete(RefreshToken).where(RefreshToken.expires_at < cutoff))

    async def get_active_user(self, db: AsyncSession, user_id: str) -> User | None:
        try:
            parsed = uuid.UUID(user_id)
        except ValueError:
            return None
        result = await db.execute(select(User).where(User.id == parsed))
        user = result.scalar_one_or_none()
        if user is not None and not user.is_active:
            return None
        return user


auth_service = AuthService()
