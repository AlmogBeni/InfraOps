"""Administration schemas: vCenters, credentials, settings, and roles."""

from __future__ import annotations

import datetime as dt
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

HOSTNAME_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-\.]{0,251}[a-zA-Z0-9])?)$"
)
SECRET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.\-]{1,148}$")


class VCenterConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=150)
    host: str = Field(min_length=3, max_length=255, pattern=HOSTNAME_PATTERN.pattern)
    port: int = Field(default=443, ge=1, le=65535)
    username_secret_ref: str = Field(min_length=2, max_length=150, pattern=SECRET_NAME_PATTERN.pattern)
    password_secret_ref: str = Field(min_length=2, max_length=150, pattern=SECRET_NAME_PATTERN.pattern)
    verify_ssl: bool = True
    notes: str = Field(default="", max_length=2000)


class VCenterConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=150)
    host: str | None = Field(default=None, min_length=3, max_length=255, pattern=HOSTNAME_PATTERN.pattern)
    port: int | None = Field(default=None, ge=1, le=65535)
    username_secret_ref: str | None = Field(
        default=None, min_length=2, max_length=150, pattern=SECRET_NAME_PATTERN.pattern
    )
    password_secret_ref: str | None = Field(
        default=None, min_length=2, max_length=150, pattern=SECRET_NAME_PATTERN.pattern
    )
    verify_ssl: bool | None = None
    enabled: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class VCenterConnectionAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    host: str
    port: int
    username_secret_ref: str
    password_secret_ref: str
    verify_ssl: bool
    enabled: bool
    notes: str
    connection_state: str
    last_connection_error: str | None = None
    last_checked_at: dt.datetime | None = None


class SecretReferenceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=150, pattern=SECRET_NAME_PATTERN.pattern)
    provider: str = Field(default="env", pattern=r"^(env|vault)$")
    description: str = Field(default="", max_length=1000)
    meta: dict = Field(default_factory=dict)


class SecretReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    description: str
    meta: dict
    created_at: dt.datetime | None = None


class DefaultTimeouts(BaseModel):
    clone_minutes: int = Field(default=30, ge=1, le=240)
    vmware_tools_minutes: int = Field(default=15, ge=1, le=120)
    network_configuration_minutes: int = Field(default=5, ge=1, le=60)
    guest_operations_minutes: int = Field(default=10, ge=1, le=120)


class PlatformSettingsOut(BaseModel):
    vm_name_policy_regex: str
    allowed_installer_roots: list[str]
    default_timeouts: DefaultTimeouts
    environment_label: str


class PlatformSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vm_name_policy_regex: str | None = Field(default=None, max_length=300)
    allowed_installer_roots: list[str] | None = Field(default=None, max_length=20)
    default_timeouts: DefaultTimeouts | None = None
    environment_label: str | None = Field(default=None, max_length=40)

    @field_validator("vm_name_policy_regex")
    @classmethod
    def _regex_must_compile(cls, value: str | None) -> str | None:
        if value is not None:
            re.compile(value)
        return value


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
