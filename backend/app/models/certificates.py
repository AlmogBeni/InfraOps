"""Certificate packages and certificates deployed to Windows guests."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    Enum as SaEnum,
    ForeignKey,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, updated_at_column, uuid_primary_key


class CertificateType(str, enum.Enum):
    ROOT = "ROOT"
    INTERMEDIATE = "INTERMEDIATE"


class CertificateStore(str, enum.Enum):
    """Windows LocalMachine certificate store names used by deployment."""

    ROOT = "Root"  # Trusted Root Certification Authorities
    CA = "CA"  # Intermediate Certification Authorities


class CertificatePackage(Base):
    """A selectable bundle of certificates shown to operators during provisioning."""

    __tablename__ = "certificate_packages"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="package", cascade="all, delete-orphan", lazy="selectin"
    )


class Certificate(Base):
    """A single X.509 certificate registered by an administrator.

    Only public certificate material (PEM body) is stored. Fingerprints are
    computed server-side at registration time and used for idempotent,
    verified deployment into the guest's computer-account stores.
    """

    __tablename__ = "certificates"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    package_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("certificate_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    friendly_name: Mapped[str] = mapped_column(String(200), nullable=False)
    certificate_type: Mapped[CertificateType] = mapped_column(
        SaEnum(CertificateType, name="certificate_type"), nullable=False
    )
    destination_store: Mapped[CertificateStore] = mapped_column(
        SaEnum(CertificateStore, name="certificate_store"), nullable=False
    )
    subject_cn: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    not_before: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    not_after: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    pem_body: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()

    package: Mapped[CertificatePackage] = relationship(back_populates="certificates")
