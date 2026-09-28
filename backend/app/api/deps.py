"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_permission
from app.auth.permissions import Permission
from app.core.client_ip import client_ip_from_request
from app.db.session import get_db
from app.models.user import User

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


def require(permission: Permission):  # noqa: ANN201 - returns a Depends wrapper
    """Route dependency enforcing a single permission."""
    return Depends(require_permission(permission))


async def get_client_ip(request: Request) -> str | None:
    return client_ip_from_request(request)


ClientIp = Annotated[str | None, Depends(get_client_ip)]
