"""Authentication endpoints (local development mode; LDAP/OIDC plug in later)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request, Response, status

from app.api.deps import ClientIp, CurrentUser, DbSession
from app.audit.actions import AuditAction
from app.audit.recorder import AuditRecorder
from app.auth.permissions import permissions_for_roles
from app.auth.service import RefreshTokenReuseError, auth_service
from app.core.config import get_settings
from app.core.errors import AuthenticationError, RateLimitError
from app.core.logging import bind_logging_context, get_logger
from app.core.rate_limit import get_login_rate_limiter
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
    limiter = get_login_rate_limiter()
    limit = get_settings().rate_limit_login_per_minute
    # Per client IP and per username: the second bounds distributed guessing
    # against one account even if many source addresses are used.
    allowed_ip = await limiter.allow(f"login-ip:{source_ip or 'unknown'}", limit)
    allowed_user = await limiter.allow(f"login-user:{payload.username.casefold()}", limit)
    if not (allowed_ip and allowed_user):
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
        # Commit explicitly: the request session rolls back on the raised error.
        await db.commit()
        raise

    await auth_service.purge_expired_refresh_tokens(db)
    tokens = await auth_service.issue_tokens(db, user)
    access_token, expires_at, refresh_token = (
        tokens.access_token, tokens.access_expires_at, tokens.refresh_token
    )
    user.last_login_at = dt.datetime.now(dt.UTC)
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
        expires_in=max(0, int((expires_at - dt.datetime.now(dt.UTC)).total_seconds())),
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
    try:
        user, tokens = await auth_service.rotate_refresh_token(db, refresh_token)
    except RefreshTokenReuseError as exc:
        await AuditRecorder(db).record(
            AuditAction.AUTH_REFRESH_REUSE_DETECTED,
            resource_type="user",
            resource_name=str(exc.user_id),
            result="revoked",
            source_ip=source_ip,
            details={"user_id": str(exc.user_id)},
        )
        # Persist the family revocation and the audit row despite the 401.
        await db.commit()
        raise
    except AuthenticationError:
        await db.commit()
        raise
    access_token, expires_at = tokens.access_token, tokens.access_expires_at
    await db.commit()
    _set_refresh_cookie(response, tokens.refresh_token)
    return TokenResponse(
        access_token=access_token,
        expires_in=max(0, int((expires_at - dt.datetime.now(dt.UTC)).total_seconds())),
        user=UserOut(
            id=str(user.id),
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=user.role_names,
            permissions=permissions_for_roles(user.role_names),
        ),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
)
async def logout(request: Request, db: DbSession, source_ip: ClientIp) -> Response:
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    user_id = (
        await auth_service.revoke_refresh_token(db, refresh_token) if refresh_token else None
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        REFRESH_COOKIE,
        path=f"{get_settings().api_v1_prefix}/auth",
    )
    user = await db.get(User, user_id) if user_id else None
    await AuditRecorder(db).record(
        AuditAction.AUTH_LOGOUT,
        user=user,
        result="success",
        source_ip=source_ip,
    )
    await db.commit()
    return response


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
