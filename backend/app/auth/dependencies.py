"""FastAPI dependencies enforcing authentication and authorization.

Authorization is *always* enforced here (server-side); the frontend merely
mirrors the same matrix for UX.
"""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission, roles_grant
from app.auth.service import auth_service
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.logging import bind_logging_context
from app.db.session import get_db
from app.models.user import User

_bearer = HTTPBearer(auto_error=False)


def _resolve_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    # Tokens are accepted only in the Authorization header. The SSE stream
    # uses single-use stream tickets instead (see auth/stream_tickets.py).
    if credentials is not None and credentials.credentials:
        return credentials.credentials
    raise AuthenticationError("Missing bearer token.")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _resolve_token(credentials)
    payload = auth_service.decode_access(token)
    user = await auth_service.get_active_user(db, str(payload["sub"]))
    if user is None:
        raise AuthenticationError("Account not found or inactive.")
    bind_logging_context(user_id=str(user.id))
    return user


def require_permission(permission: Permission):
    """Dependency factory: grants access only when any user role grants it."""

    async def checker(user: User = Depends(get_current_user)) -> User:
        if not roles_grant(user.role_names, permission):
            raise AuthorizationError(
                f"Your roles ({', '.join(user.role_names) or 'none'}) do not grant '{permission.value}'."
            )
        return user

    return checker
