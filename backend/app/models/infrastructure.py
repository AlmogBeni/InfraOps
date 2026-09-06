"""VMware infrastructure connection registry."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, created_at_column, updated_at_column, uuid_primary_key


class VCenterConnection(Base):
    """A registered vCenter endpoint.

    Credentials are never stored here — only secret references resolved at
    runtime through encrypted backend credential storage.
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

    @property
    def display_state(self) -> str:
        if not self.enabled:
            return "disabled"
        return self.last_connection_state or "unknown"
