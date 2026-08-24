"""Internal application catalog with dependency graph support.

Only administrators may define applications — including installer paths and
silent-install arguments. Operators can merely select from the approved,
enabled catalog entries during provisioning.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum as SaEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, updated_at_column, uuid_primary_key


class InstallerType(str, enum.Enum):
    MSI = "MSI"
    EXE = "EXE"
    POWERSHELL = "POWERSHELL"


class DetectionMethod(str, enum.Enum):
    MSI_PRODUCT_CODE = "MSI_PRODUCT_CODE"
    REGISTRY_KEY = "REGISTRY_KEY"
    FILE_EXISTS = "FILE_EXISTS"
    SERVICE_EXISTS = "SERVICE_EXISTS"
    SCRIPT = "SCRIPT"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    installer_type: Mapped[InstallerType] = mapped_column(
        SaEnum(InstallerType, name="installer_type"), nullable=False
    )
    installer_path: Mapped[str] = mapped_column(String(500), nullable=False)
    install_arguments: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detection_method: Mapped[DetectionMethod] = mapped_column(
        SaEnum(DetectionMethod, name="detection_method"), nullable=False
    )
    detection_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    reboot_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()

    dependencies: Mapped[list["ApplicationDependency"]] = relationship(
        foreign_keys="ApplicationDependency.app_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ApplicationDependency(Base):
    """Directed edge: ``app`` requires ``depends_on`` to be installed first."""

    __tablename__ = "application_dependencies"
    __table_args__ = (
        UniqueConstraint("app_id", "depends_on_id", name="uq_application_dependency"),
        CheckConstraint("app_id <> depends_on_id", name="ck_no_self_dependency"),
    )

    app_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        primary_key=True,
    )
