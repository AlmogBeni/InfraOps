"""Administration schemas: vCenters, credentials, settings, and roles."""

from __future__ import annotations

import datetime as dt
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.secret_references import SECRET_REFERENCE_PATTERN

HOSTNAME_PATTERN = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-\.]{0,251}[a-zA-Z0-9])?)$"
)
# Backward-compatible name retained for schema consumers.
SECRET_NAME_PATTERN = SECRET_REFERENCE_PATTERN


def _require_tls_verification_in_production(value: bool | None) -> bool | None:
    from app.services.vmware.base import insecure_vcenter_tls_permitted

    if value is False and not insecure_vcenter_tls_permitted():
        raise ValueError(
            "TLS certificate verification cannot be disabled in production. Trust the vCenter CA "
            "(VCENTER_CA_FILE), or set ALLOW_INSECURE_VCENTER_TLS=true to accept the risk."
        )
    return value


class VCenterConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=150)
    host: str = Field(min_length=3, max_length=255, pattern=HOSTNAME_PATTERN.pattern)
    port: int = Field(default=443, ge=1, le=65535)
    username_secret_ref: str = Field(min_length=2, max_length=150, pattern=SECRET_NAME_PATTERN.pattern)
    password_secret_ref: str = Field(min_length=2, max_length=150, pattern=SECRET_NAME_PATTERN.pattern)
    verify_ssl: bool = True
    notes: str = Field(default="", max_length=2000)

    @field_validator("verify_ssl")
    @classmethod
    def _tls_policy(cls, value: bool | None) -> bool | None:
        return _require_tls_verification_in_production(value)


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

    @field_validator("verify_ssl")
    @classmethod
    def _tls_policy(cls, value: bool | None) -> bool | None:
        return _require_tls_verification_in_production(value)


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
    provider: str = Field(default="database", pattern=r"^(database)$")
    purpose: str = Field(
        default="generic",
        pattern=r"^(generic|vcenter|guest_administrator|domain_join)$",
    )
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    description: str = Field(default="", max_length=1000)
    meta: dict = Field(default_factory=dict)


class SecretReferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str | None = Field(
        default=None,
        pattern=r"^(generic|vcenter|guest_administrator|domain_join)$",
    )
    username: str | None = Field(default=None, min_length=1, max_length=320)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    description: str | None = Field(default=None, max_length=1000)


class SecretReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    purpose: str
    description: str
    meta: dict
    configured: bool
    revision: int
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None


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

    @field_validator("allowed_installer_roots")
    @classmethod
    def _roots_must_be_paths(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        from app.services.applications.paths import validate_installer_root

        return [validate_installer_root(root) for root in value]


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
