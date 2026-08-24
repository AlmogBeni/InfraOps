"""Password hashing and JWT token utilities.

* Passwords use scrypt (memory-hard, stdlib only) with per-password salt.
* Access tokens are short-lived JWTs; refresh tokens are longer-lived JWTs
  delivered exclusively via HttpOnly cookies.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from typing import Any, Literal

import jwt

from app.core.config import get_settings
from app.core.errors import AuthenticationError

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 64

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _create_token(
    subject: str,
    token_type: TokenType,
    expires_minutes: int,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, str, dt.datetime]:
    settings = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(minutes=expires_minutes)
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "infraops",
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, jti, expires_at


def create_access_token(subject: str, roles: list[str]) -> tuple[str, dt.datetime]:
    token, _, expires_at = _create_token(
        subject, "access", get_settings().access_token_expire_minutes, {"roles": roles}
    )
    return token, expires_at


def create_refresh_token(subject: str) -> tuple[str, str, dt.datetime]:
    return _create_token(subject, "refresh", get_settings().refresh_token_expire_minutes)


def decode_token(token: str, expected_type: TokenType = "access") -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"], issuer="infraops")
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Session token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid session token.") from exc
    if payload.get("type") != expected_type:
        raise AuthenticationError("Invalid token type.")
    return payload
