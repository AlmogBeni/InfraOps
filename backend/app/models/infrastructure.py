"""VMware infrastructure connection registry and logical sites."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, created_at_column, updated_at_column, uuid_primary_key


class VCenterConnection(Base):
    """A registered vCenter endpoint.

    Credentials are never stored here — only secret references resolved at
    runtime through the secrets provider.
    """

    __tablename__ = "vcenters"

    id: Mapped[uuid.UUID] = uuid_primary_key()
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=443)
    username_secret_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    password_secret_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_connection_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_connection_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()

    sites: Mapped[list["Site"]] = relationship(back_populates="vcenter", cascade="all, delete-orphan")

    @property
    def display_state(self) -> str:
        if not self.enabled:
            return "disabled"
        return self.last_connection_state or "unknown"


class Site(Base):
    """Logical target site (maps to a vCenter and optionally a datacenter)."""

    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("vcenter_id", "name", name="uq_sites_vcenter_name"),)

    id: Mapped[uuid.UUID] = uuid_primary_key()
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    vcenter_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("vcenters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    datacenter_moref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = created_at_column()
    updated_at: Mapped[dt.datetime] = updated_at_column()

    vcenter: Mapped[VCenterConnection] = relationship(back_populates="sites")
