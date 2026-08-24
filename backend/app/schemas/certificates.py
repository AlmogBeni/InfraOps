"""Certificate catalog schemas."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, UUID4

from app.models.certificates import CertificateStore, CertificateType


class CertificateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    package_id: str
    friendly_name: str
    certificate_type: CertificateType
    destination_store: CertificateStore
    subject_cn: str
    fingerprint_sha256: str
    not_before: dt.date | None = None
    not_after: dt.date | None = None
    file_name: str
    enabled: bool


class CertificatePackageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    enabled: bool
    certificates: list[CertificateOut] = []
    created_at: dt.datetime | None = None


class CertificatePackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=150)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True


class CertificatePackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None


class CertificateCreate(BaseModel):
    """Administrators register certificates by pasting the PEM body.

    The server parses the certificate, computes the SHA-256 fingerprint and
    extracts validity dates — clients cannot forge fingerprints.
    """

    model_config = ConfigDict(extra="forbid")

    package_id: UUID4
    friendly_name: str = Field(min_length=2, max_length=200)
    certificate_type: CertificateType
    pem_body: str = Field(min_length=64, max_length=100_000)
    enabled: bool = True


class CertificateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    friendly_name: str | None = Field(default=None, min_length=2, max_length=200)
    enabled: bool | None = None
