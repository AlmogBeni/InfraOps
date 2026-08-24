"""Authentication endpoints (local development mode; LDAP/OIDC plug in later)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from app.api.deps import ClientIp, CurrentUser, DbSession
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.dependencies import get_current_user
from app.auth.permissions import permissions_for_roles
from app.auth.service import auth_service
from app.core.config import get_settings
from app.core.errors import AuthenticationError, RateLimitError
from app.core.logging import bind_logging_context, get_logger
from app.core.rate_limit import login_rate_limiter
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "infraops_refresh"


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_expire_minutes * 60,
        path=f"{settings.api_v1_prefix}/auth",
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, response: Response, db: DbSession, source_ip: ClientIp) -> TokenResponse:
    if not login_rate_limiter.allow(f"login:{source_ip or 'unknown'}",
                                    get_settings().rate_limit_login_per_minute):
        raise RateLimitError("Too many login attempts — try again shortly.")

    try:
        user = await auth_service.authenticate(db, payload.username, payload.password)
    except AuthenticationError:
        await AuditRecorder(db).record(
            AuditAction.AUTH_LOGIN_FAILED,
            username=payload.username,
            resource_type="user",
            resource_name=payload.username,
            result="failure",
            source_ip=source_ip,
        )
        raise

    access_token, expires_at, refresh_token = await auth_service.issue_tokens(user)
    user.last_login_at = dt.datetime.now(dt.timezone.utc)
    db.add(user)

    await AuditRecorder(db).record(
        AuditAction.AUTH_LOGIN,
        user=user,
        resource_type="user",
        resource_name=user.username,
        result="success",
        source_ip=source_ip,
    )
    await db.commit()
    bind_logging_context(user_id=str(user.id))
    _set_refresh_cookie(response, refresh_token)

    return TokenResponse(
        access_token=access_token,
        expires_in=max(0, int((expires_at - dt.datetime.now(dt.timezone.utc)).total_seconds())),
        user=UserOut(
            id=str(user.id),
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=user.role_names,
            permissions=permissions_for_roles(user.role_names),
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    request: Request, response: Response, db: DbSession, source_ip: ClientIp
) -> TokenResponse:
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise AuthenticationError("Missing refresh token.")
    user = await auth_service.user_from_refresh_token(db, refresh_token)
    access_token, expires_at, new_refresh = await auth_service.issue_tokens(user)
    _set_refresh_cookie(response, new_refresh)
    return TokenResponse(
        access_token=access_token,
        expires_in=max(0, int((expires_at - dt.datetime.now(dt.timezone.utc)).total_seconds())),
        user=UserOut(
            id=str(user.id),
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=user.role_names,
            permissions=permissions_for_roles(user.role_names),
        ),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, db: DbSession, source_ip: ClientIp) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=f"{get_settings().api_v1_prefix}/auth")
    await AuditRecorder(db).record(AuditAction.AUTH_LOGOUT, result="success", source_ip=source_ip)


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> UserOut:
    return UserOut(
        id=str(current_user.id),
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        roles=current_user.role_names,
        permissions=permissions_for_roles(current_user.role_names),
    )
