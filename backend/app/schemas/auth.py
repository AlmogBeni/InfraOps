"""Authentication schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    full_name: str | None = None
    roles: list[str] = []
    permissions: list[str] = []


class SessionPolicyOut(BaseModel):
    """Public session settings the browser needs before and after sign-in."""

    idle_timeout_seconds: int
    idle_warning_seconds: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
